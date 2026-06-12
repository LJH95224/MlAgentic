"""V2.0 统一查询接口 /api/v2/query（UQA-01）。

串联全链路：查询 → 图谱锚定（T8 才接通，当前 skip）→ hybrid_search → rerank
→ build_context → LLM 生成 → parse_citations → 返回响应。

支持流式（SSE）和非流式两种模式。T6 阶段先实现非流式。
"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.config import get_settings
from app.observability.tracer import Tracer
from app.rag.citation import (
    build_context_with_citation,
    build_citation_system_prompt,
    parse_citations,
)
from app.rag.hybrid_retriever import hybrid_search, format_hybrid_results
from app.schemas.v2.query import (
    CitationItem,
    QueryOptions,
    QueryRequest,
    QueryResponse,
)
logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/query", response_model=QueryResponse)
async def v2_query(
    body: QueryRequest,
    db: AsyncSession = Depends(get_db),
) -> QueryResponse:
    """V2.0 统一查询接口（UQA-01）。

    非流式模式：返回完整答案 + 引用列表 + trace_id。
    """
    start_time = time.perf_counter()
    settings = get_settings()

    # ── Trace 上下文 ──
    async with Tracer(session_id=body.session_id, kb_id=body.kb_ids[0] if body.kb_ids else None) as tracer:
        # Step 1: 混合检索
        with tracer.step("retrieve", step_input={"query": body.query[:100], "top_k": body.options.top_k}):
            results = await hybrid_search(
                query=body.query,
                top_k=body.options.top_k,
            )
            # 转换为 citation 需要的格式
            chunks_for_citation = [
                {
                    "document_name": (r.metadata or {}).get("filename", r.document_id),
                    "page_number": r.page_number,
                    "content": r.content,
                    "chunk_id": r.chunk_id,
                    "heading_path": r.heading_path,
                    "rerank_score": r.score,
                }
                for r in results
            ]

        if not results:
            return QueryResponse(
                answer="抱歉，未检索到相关内容。请尝试更换关键词或放宽搜索范围。",
                source_citations=[],
                trace_id=tracer.trace_id,
            )

        # Step 2: 构建 context + 引用标记
        with tracer.step("build_context", step_input={"chunks": len(chunks_for_citation)}):
            context = build_context_with_citation(chunks_for_citation)

        # Step 3: LLM 生成
        with tracer.step("generate", step_input={"model": settings.litellm_model}) as gen_step:
            answer = await _generate_answer(
                query=body.query,
                context=context,
                session_id=body.session_id,
                db=db,
            )
            gen_step.step_output = {"answer_len": len(answer)}

        # Step 4: 解析引用
        with tracer.step("citation_parse", step_input={"answer_len": len(answer)}):
            source_citations = parse_citations(answer, chunks_for_citation)

    total_latency_ms = int((time.perf_counter() - start_time) * 1000)

    # 构建 citation items
    citation_items = [
        CitationItem(
            chunk_id=c.get("chunk_id"),
            document_name=c.get("document_name", ""),
            page_number=c.get("page_number"),
            heading_path=c.get("heading_path", []),
            snippet=c.get("snippet", ""),
            rerank_score=c.get("rerank_score"),
        )
        for c in source_citations
    ]

    return QueryResponse(
        answer=answer,
        source_citations=citation_items,
        trace_id=tracer.trace_id,
        total_latency_ms=total_latency_ms,
    )


async def _generate_answer(
    *,
    query: str,
    context: str,
    session_id: uuid.UUID | None,
    db: AsyncSession,
) -> str:
    """调用 LLM 生成答案。

    使用 LiteLLM acompletion，注入 citation 规则的 system prompt。
    """
    import litellm

    settings = get_settings()

    system_prompt = (
        "你是一个气象空间智能助手。请基于以下检索结果回答用户问题。\n\n"
        f"{build_citation_system_prompt()}\n\n"
        f"检索结果：\n{context}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query},
    ]

    try:
        response = await litellm.acompletion(
            model=settings.litellm_model,
            messages=messages,
            api_key=settings.litellm_api_key,
            api_base=settings.litellm_api_base,
            temperature=0.3,
            max_tokens=2000,
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.error("V2 query LLM 生成失败: %s", e)
        return f"生成答案时遇到错误：{type(e).__name__}。请稍后重试。"
