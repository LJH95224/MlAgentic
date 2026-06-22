"""V2.0 UQA-03 纯生成子接口 POST /api/v2/generate。

接受开发者自定义的 context_chunks，跳过检索步骤，
直接调 LLM 生成答案 + Citation 溯源 + 答案自检。
不触发任何 Milvus / Neo4j 查询。
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import error_codes
from app.api.deps import get_db
from app.api.exceptions import BusinessError
from app.core.config import get_settings
from app.llm.client import build_completion_kwargs
from app.rag.citation import (
    build_citation_system_prompt,
    build_context_with_citation,
    parse_citations,
)
from app.rag.confidence import ConfidenceScore, compute_confidence
from app.rag.faithfulness import (
    DISABLED_RESULT,
    FaithfulnessResult,
    append_unverified_warning,
    check_faithfulness,
)
from app.schemas.v2.generate import GenerateRequest, GenerateResponse
from app.schemas.v2.query import CitationItem

logger = logging.getLogger(__name__)


def _try_int_chunk_id(value: str | int | None) -> int | None:
    """尝试将 chunk_id 转为 int（CitationItem.chunk_id 期望 int）。

    Milvus 的 chunk_id 是 INT64，但 /v2/generate 的 ContextChunk.chunk_id
    允许任意字符串。非纯数字的 chunk_id 设为 None。
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


router = APIRouter(tags=["V2 分层子接口"])


@router.post("/generate", response_model=GenerateResponse)
async def v2_generate(
    body: GenerateRequest,
    db: AsyncSession = Depends(get_db),
) -> GenerateResponse:
    """UQA-03 纯生成：自定义 context + LLM + Citation + 自检。"""
    start = time.perf_counter()
    settings = get_settings()

    # 防御性校验（Schema 已有 min_length=1，但端点层也做兜底）
    if not body.context_chunks:
        raise BusinessError(
            error_codes.CONTEXT_CHUNKS_EMPTY,
            "传入的上下文块列表为空",
        )

    # 将 ContextChunk 转换为 citation 模块需要的格式
    chunks_for_citation = [
        {
            "document_name": c.source_label or c.chunk_id,
            "page_number": None,
            "content": c.content,
            "chunk_id": c.chunk_id,
            "heading_path": [],
            "rerank_score": None,
        }
        for c in body.context_chunks
    ]

    # 构建 context（与 /v2/query 同款，含 [1][2] 引用标记）
    if body.options.enable_citation:
        context = build_context_with_citation(chunks_for_citation)
    else:
        # 不启用 Citation 时，简单拼接内容
        context = "\n\n".join(c.content for c in body.context_chunks)

    # LLM 生成
    try:
        answer = await _generate_answer(
            query=body.query,
            context=context,
            enable_citation_prompt=body.options.enable_citation,
        )
    except Exception as e:
        logger.error("Generate LLM 失败: %s", e, exc_info=True)
        total_latency_ms = int((time.perf_counter() - start) * 1000)
        return GenerateResponse(
            answer=f"答案生成失败：{type(e).__name__}。请稍后重试。",
            source_citations=[],
            confidence=0.0,
            faithfulness_check="skipped",
            trace_id="",
            total_latency_ms=total_latency_ms,
        )

    # Citation 解析
    raw_citations = []
    if body.options.enable_citation:
        raw_citations = parse_citations(answer, chunks_for_citation)

    source_citations = [
        CitationItem(
            chunk_id=_try_int_chunk_id(c.get("chunk_id")),
            document_name=c.get("document_name", ""),
            page_number=c.get("page_number"),
            heading_path=c.get("heading_path", []),
            snippet=c.get("snippet", ""),
            rerank_score=c.get("rerank_score"),
        )
        for c in raw_citations
    ]

    # 答案自检（CHC-04）
    faith_result: FaithfulnessResult = DISABLED_RESULT
    if body.options.enable_faithfulness_check:
        try:
            faith_result = await check_faithfulness(answer=answer, context=context)
        except Exception as e:
            logger.warning("Generate faithfulness 失败（软降级）: %s", e)
            faith_result = FaithfulnessResult(status="skipped")
        if faith_result.unverified:
            answer = append_unverified_warning(answer, faith_result.unverified)

    # 置信度评分（CHC-03）
    score: ConfidenceScore = compute_confidence(
        cited_chunks=raw_citations,
        top_k=len(body.context_chunks),
        hallucination_penalty=faith_result.hallucination_penalty,
    )

    total_latency_ms = int((time.perf_counter() - start) * 1000)

    return GenerateResponse(
        answer=answer,
        source_citations=source_citations,
        confidence=score.confidence,
        low_confidence_warning=score.low_confidence_warning,
        faithfulness_check=faith_result.status,
        unverified_claims=faith_result.unverified or None,
        trace_id="",
        total_latency_ms=total_latency_ms,
    )


async def _generate_answer(
    *,
    query: str,
    context: str,
    enable_citation_prompt: bool = True,
) -> str:
    """调用 LLM 生成答案（UQA-03 专用）。

    与 /v2/query 的 generate_answer 类似，但：
    - 支持 enable_citation_prompt 开关
    - 不依赖 session_id / db（纯生成场景无历史）
    """
    import litellm

    settings = get_settings()

    citation_prompt = build_citation_system_prompt() if enable_citation_prompt else ""
    system_prompt = (
        "你是一个气象空间智能助手。请基于以下提供的上下文回答用户问题。\n\n"
        f"{citation_prompt}\n\n"
        f"上下文：\n{context}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query},
    ]

    hard_timeout = settings.litellm_timeout * (settings.litellm_num_retries + 1) + 10

    try:
        litellm.num_retries = settings.litellm_num_retries
        kwargs = build_completion_kwargs(
            messages=messages,
            model=settings.litellm_model,
            required_model_label="LITELLM_MODEL",
            temperature=0.3,
            max_tokens=2000,
            settings_obj=settings,
        )
        response = await asyncio.wait_for(
            litellm.acompletion(**kwargs),
            timeout=hard_timeout,
        )
        return response.choices[0].message.content or ""
    except asyncio.TimeoutError:
        logger.error("Generate LLM 超时（%.0fs）", hard_timeout)
        return "抱歉，答案生成超时，请稍后重试。"
    except Exception as e:
        logger.error("Generate LLM 失败: %s", e)
        raise
