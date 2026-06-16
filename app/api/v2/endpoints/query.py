"""V2.0 统一查询接口 /api/v2/query（UQA-01 + HRE-01/02/06，T8 完整版）。

完整链路（T8 起）：

    三层配置合并 → Query 改写（HyDE / multi_query）→ Query NER → 图谱锚定
        → 混合检索（hybrid + entity_tags 过滤；multi_query 时 RRF 二次融合）
        → build_context → LLM 生成 → parse_citations → 返回响应

支持流式（SSE）和非流式两种模式。T6/T8 阶段先实现非流式。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.agent.context import reset_current_kb_ids, set_current_kb_ids
from app.core.config import get_settings
from app.models.knowledge_base import KnowledgeBase
from app.observability.tracer import Tracer
from app.rag.citation import (
    build_context_with_citation,
    build_citation_system_prompt,
    parse_citations,
)
from app.rag.confidence import ConfidenceScore, compute_confidence
from app.rag.faithfulness import (
    DISABLED_RESULT,
    FaithfulnessResult,
    append_unverified_warning,
    check_faithfulness,
)
from app.rag.hybrid_retriever import HybridSearchResult, hybrid_search
from app.rag.query_ner import anchor_to_graph, extract_query_entities
from app.rag.query_rewriter import RewriteResult, rewrite_query
from app.rag.retrieval_config import ResolvedRetrievalOptions, resolve_options
from app.schemas.v2.query import (
    CitationItem,
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
    """V2.0 统一查询接口（UQA-01 / HRE-01/02/06）。

    非流式模式：返回完整答案 + 引用列表 + trace_id + Query 增强可观测信息。
    """
    start_time = time.perf_counter()
    settings = get_settings()

    # ── Step 0: 加载 KB（多 KB 时取第一个，本期限制；后续按需演进） ──
    kb_obj: KnowledgeBase | None = None
    if body.kb_ids:
        # session.get 仅按 PK 主键查；取不到返 None,不抛错
        kb_obj = await db.get(KnowledgeBase, body.kb_ids[0])

    # 三层合并配置（API > KB > settings）；query_rewrite 非法值在此抛 BusinessError(40011)
    resolved = resolve_options(options=body.options, kb=kb_obj, settings=settings)

    # KB-06 contextvar：让下游 hybrid_search / kg.tool 等通过 contextvar 拿到 kb_ids
    # （V1.5 chat_service 同款机制；T6 阶段遗漏，T9 联调阶段补上）
    kb_ids_token = set_current_kb_ids(body.kb_ids)
    try:
        # 整体请求硬超时保护：防止单步 LLM/Milvus 卡死导致请求无限挂起
        return await asyncio.wait_for(
            _v2_query_inner(
                body=body, db=db,
                settings=settings, kb_obj=kb_obj, resolved=resolved,
                start_time=start_time,
            ),
            timeout=settings.query_total_timeout_s,
        )
    except asyncio.TimeoutError:
        total_latency_ms = int((time.perf_counter() - start_time) * 1000)
        logger.error(
            "V2 query 整体超时（%.0fs），kb_ids=%s query=%r",
            settings.query_total_timeout_s,
            body.kb_ids,
            body.query[:60],
        )
        return QueryResponse(
            answer=f"抱歉，查询处理超时（{settings.query_total_timeout_s:.0f}s），请稍后重试或简化查询。",
            source_citations=[],
            trace_id="",
            total_latency_ms=total_latency_ms,
            confidence=0.0,
            low_confidence_warning="查询超时，结果可能不完整。",
            faithfulness_check="skipped",
        )
    finally:
        reset_current_kb_ids(kb_ids_token)


async def _v2_query_inner(
    *,
    body: QueryRequest,
    db: AsyncSession,
    settings,
    kb_obj: KnowledgeBase | None,
    resolved: ResolvedRetrievalOptions,
    start_time: float,
) -> QueryResponse:
    """v2_query 的主体逻辑；contextvar 在外层 try/finally 包好。"""
    # ── Trace 上下文 ──
    async with Tracer(
        session_id=body.session_id,
        kb_id=body.kb_ids[0] if body.kb_ids else None,
    ) as tracer:
        # ── Step 1: Query 改写（HRE-01）──
        with tracer.step(
            "query_rewrite",
            step_input={"strategy": resolved.query_rewrite, "query_len": len(body.query)},
        ) as rew_step:
            rewrite_result = await rewrite_query(body.query, resolved.query_rewrite)
            rew_step.step_output = {
                "rewritten_len": len(rewrite_result.rewritten_text or ""),
                "sub_query_count": len(rewrite_result.sub_queries),
            }

        # ── Step 2: Query NER（HRE-02）──
        with tracer.step(
            "query_ner",
            step_input={"enabled": resolved.enable_graph_rag, "query_len": len(body.query)},
        ) as ner_step:
            ner_entities: list[dict] = (
                await extract_query_entities(body.query) if resolved.enable_graph_rag else []
            )
            ner_step.step_output = {"entity_count": len(ner_entities)}

        # ── Step 3: 图谱锚定 ──
        with tracer.step(
            "graph_anchor",
            step_input={"entity_count": len(ner_entities)},
        ) as anchor_step:
            kb_ids_str = [str(k) for k in body.kb_ids] if body.kb_ids else None
            entity_tags: list[str] = (
                await anchor_to_graph(ner_entities, kb_ids_str) if ner_entities else []
            )
            anchor_step.step_output = {"tag_count": len(entity_tags)}

        # ── Step 4: 混合检索（按改写策略分支）──
        with tracer.step(
            "retrieve",
            step_input={
                "query_rewrite": resolved.query_rewrite,
                "top_k": resolved.top_k,
                "entity_tag_count": len(entity_tags),
            },
        ) as retrieve_step:
            try:
                results = await _do_retrieve(
                    body=body,
                    rewrite_result=rewrite_result,
                    entity_tags=entity_tags,
                    resolved=resolved,
                    settings=settings,
                )
            except Exception as e:  # noqa: BLE001
                logger.error("V2 query 检索失败: %s", e, exc_info=True)
                results = []
                retrieve_step.error_message = f"{type(e).__name__}: {e}"
            retrieve_step.step_output = {"hit_count": len(results)}

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
            # 检索为空兜底：confidence=0 + warning，自检按开关标 disabled/skipped
            empty_score = compute_confidence(
                cited_chunks=[], top_k=resolved.top_k, hallucination_penalty=0.0,
            )
            empty_faith_status = (
                "skipped" if resolved.enable_faithfulness_check else "disabled"
            )
            return QueryResponse(
                answer="抱歉，未检索到相关内容。请尝试更换关键词或放宽搜索范围。",
                source_citations=[],
                trace_id=tracer.trace_id,
                rewritten_query=rewrite_result.rewritten_text,
                sub_queries=rewrite_result.sub_queries or None,
                ner_entities=ner_entities or None,
                graph_anchored_tags=entity_tags or None,
                confidence=empty_score.confidence,
                low_confidence_warning=empty_score.low_confidence_warning,
                faithfulness_check=empty_faith_status,
                unverified_claims=None,
            )

        # ── Step 5: 构建 context + 引用标记 ──
        with tracer.step("build_context", step_input={"chunks": len(chunks_for_citation)}):
            context = build_context_with_citation(chunks_for_citation)

        # ── Step 6: LLM 生成 ──
        with tracer.step("generate", step_input={"model": settings.litellm_model}) as gen_step:
            answer = await _generate_answer(
                query=body.query,
                context=context,
                session_id=body.session_id,
                db=db,
            )
            gen_step.step_output = {"answer_len": len(answer)}

        # ── Step 7: 解析引用 ──
        with tracer.step("citation_parse", step_input={"answer_len": len(answer)}):
            source_citations = parse_citations(answer, chunks_for_citation)

        # ── Step 8 (T9): 答案自检（CHC-04）──
        # 默认 disabled；开启时跑 LLM as Judge，失败软降级为 skipped
        faith_result: FaithfulnessResult = DISABLED_RESULT
        if resolved.enable_faithfulness_check:
            with tracer.step(
                "faithfulness_check",
                step_input={"answer_len": len(answer), "context_len": len(context)},
            ) as f_step:
                faith_result = await check_faithfulness(answer=answer, context=context)
                f_step.step_output = {
                    "status": faith_result.status,
                    "claim_count": len(faith_result.claims),
                    "unverified_count": len(faith_result.unverified),
                    "penalty": faith_result.hallucination_penalty,
                }
            # 有 unverified 时把警告清单追加到 answer 末尾
            if faith_result.unverified:
                answer = append_unverified_warning(answer, faith_result.unverified)

        # ── Step 9 (T9): 置信度评分（CHC-03）──
        score: ConfidenceScore = compute_confidence(
            cited_chunks=source_citations,
            top_k=resolved.top_k,
            hallucination_penalty=faith_result.hallucination_penalty,
        )

    total_latency_ms = int((time.perf_counter() - start_time) * 1000)

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
        rewritten_query=rewrite_result.rewritten_text,
        sub_queries=rewrite_result.sub_queries or None,
        ner_entities=ner_entities or None,
        graph_anchored_tags=entity_tags or None,
        confidence=score.confidence,
        low_confidence_warning=score.low_confidence_warning,
        faithfulness_check=faith_result.status,
        unverified_claims=faith_result.unverified or None,
    )


# ──────────────────── 内部检索辅助 ────────────────────


async def _do_retrieve(
    *,
    body: QueryRequest,
    rewrite_result: RewriteResult,
    entity_tags: list[str],
    resolved: ResolvedRetrievalOptions,
    settings,
) -> list[HybridSearchResult]:
    """按 query_rewrite 策略分支执行检索。

    - none / hyde：单路 hybrid_search；hyde 时用 rewritten_text 替代 query
    - multi_query：N+1 路并发检索（N 个子查询 + 原 query），RRF 二次融合
    """
    tags_kw = entity_tags or None

    if resolved.query_rewrite == "multi_query" and rewrite_result.sub_queries:
        queries = list(rewrite_result.sub_queries) + [body.query]
        return await _multi_query_search(
            queries=queries,
            top_k=resolved.top_k,
            entity_tags=tags_kw,
            rrf_k=resolved.rrf_k,
            reranker_enable=resolved.reranker_enable,
        )

    # none / hyde / multi_query 但子查询为空（软降级）→ 单路检索
    search_text = rewrite_result.rewritten_text or body.query
    return await hybrid_search(
        query=search_text,
        top_k=resolved.top_k,
        entity_tags=tags_kw,
        reranker_enable=resolved.reranker_enable,
    )


async def _multi_query_search(
    *,
    queries: list[str],
    top_k: int,
    entity_tags: list[str] | None,
    rrf_k: int,
    reranker_enable: bool = True,
) -> list[HybridSearchResult]:
    """N 路检索结果 RRF 二次融合，按 chunk_id 去重 + rank-based 重算分数。

    任一路失败（return_exceptions=True 收到 Exception）→ warning 跳过，其他继续。
    """
    coros = [
        hybrid_search(query=q, top_k=top_k, entity_tags=entity_tags, reranker_enable=reranker_enable) for q in queries
    ]
    raw = await asyncio.gather(*coros, return_exceptions=True)

    # RRF 累加：score(c) = Σ 1/(k + rank_i(c))
    rrf_scores: dict[int, float] = {}
    chunk_map: dict[int, HybridSearchResult] = {}
    for path_idx, results in enumerate(raw):
        if isinstance(results, Exception):
            logger.warning("multi_query 第 %d 路检索失败（已忽略）：%s",
                           path_idx, results)
            continue
        for rank, item in enumerate(results, start=1):
            cid = item.chunk_id
            if cid is None:
                # 没有 chunk_id 的结果不应进入 RRF（无法去重），跳过
                continue
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (rrf_k + rank)
            # 同一 chunk 在多路命中，保留分数最高那次的元数据
            if cid not in chunk_map or item.score > chunk_map[cid].score:
                chunk_map[cid] = item

    # 按 RRF 分数降序，取 top_k
    sorted_cids = sorted(rrf_scores.keys(), key=lambda c: rrf_scores[c], reverse=True)
    top_cids = sorted_cids[:top_k]

    # 用 RRF 分数覆盖 score 字段，便于下游 citation 看到新排名
    merged: list[HybridSearchResult] = []
    for cid in top_cids:
        item = chunk_map[cid]
        merged.append(
            HybridSearchResult(
                chunk_id=item.chunk_id,
                content=item.content,
                document_id=item.document_id,
                score=rrf_scores[cid],
                entity_tags=item.entity_tags,
                heading_path=item.heading_path,
                block_type=item.block_type,
                page_number=item.page_number,
                metadata=item.metadata,
                source_collection=item.source_collection,
            )
        )
    return merged


async def _generate_answer(
    *,
    query: str,
    context: str,
    session_id: uuid.UUID | None,
    db: AsyncSession,
) -> str:
    """调用 LLM 生成答案。

    使用 LiteLLM acompletion，注入 citation 规则的 system prompt。
    超时保护：litellm_timeout + asyncio.wait_for 双重兜底。
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

    # LLM 调用硬超时：取 litellm_timeout 的 2 倍作为 wait_for 兜底
    # （litellm 内部超时触发后会重试 num_retries 次；wait_for 防极端情况）
    hard_timeout = settings.litellm_timeout * (settings.litellm_num_retries + 1) + 10

    try:
        # num_retries 不是 litellm.acompletion 的显式参数，
        # 通过模块级设置配置重试次数
        litellm.num_retries = settings.litellm_num_retries
        response = await asyncio.wait_for(
            litellm.acompletion(
                model=settings.litellm_model,
                messages=messages,
                api_key=settings.litellm_api_key,
                api_base=settings.litellm_api_base,
                temperature=0.3,
                max_tokens=2000,
                timeout=settings.litellm_timeout,
            ),
            timeout=hard_timeout,
        )
        return response.choices[0].message.content or ""
    except asyncio.TimeoutError:
        logger.error("V2 query LLM 生成超时（%.0fs），返回兜底文案", hard_timeout)
        return "抱歉，答案生成超时，请稍后重试。"
    except Exception as e:
        logger.error("V2 query LLM 生成失败: %s", e)
        return f"生成答案时遇到错误：{type(e).__name__}。请稍后重试。"
