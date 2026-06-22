"""V2.0 UQA-02 纯检索子接口 POST /api/v2/retrieve。

只执行检索（hybrid_search），不调用 LLM 生成答案。
返回经过混合检索 + RRF + Reranker 处理后的 Chunk 列表。
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.agent.context import reset_current_kb_ids, set_current_kb_ids
from app.core.config import get_settings
from app.observability.tracer import Tracer
from app.rag.hybrid_retriever import hybrid_search
from app.rag.query_ner import anchor_to_graph, extract_query_entities
from app.schemas.v2.retrieve import RetrieveChunkItem, RetrieveRequest, RetrieveResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["V2 分层子接口"])


@router.post("/retrieve", response_model=RetrieveResponse)
async def v2_retrieve(
    body: RetrieveRequest,
    db: AsyncSession = Depends(get_db),
) -> RetrieveResponse:
    """UQA-02 纯检索：返回 chunks 列表，不调 LLM。"""
    start = time.perf_counter()
    # KB contextvar
    kb_ids_token = set_current_kb_ids(body.kb_ids)
    try:
        async with Tracer(
            kb_id=body.kb_ids[0] if body.kb_ids else None,
        ) as tracer:
            try:
                # Graph RAG：NER + 锚定（与 /v2/query 同款，但简化为无条件跟随配置）
                entity_tags: list[str] = []
                with tracer.step(
                    "query_ner",
                    step_input={"enabled": bool(body.enable_graph_rag), "query_len": len(body.query)},
                ) as ner_step:
                    ner_entities = []
                    if body.enable_graph_rag:
                        try:
                            ner_entities = await extract_query_entities(body.query)
                        except Exception as e:
                            logger.warning("Retrieve Query NER 失败（已忽略）: %s", e)
                            ner_step.error_message = f"{type(e).__name__}: {e}"
                    ner_step.step_output = {"entity_count": len(ner_entities)}

                with tracer.step(
                    "graph_anchor",
                    step_input={"entity_count": len(ner_entities)},
                ) as anchor_step:
                    if ner_entities:
                        try:
                            kb_ids_str = [str(k) for k in body.kb_ids] if body.kb_ids else None
                            entity_tags = await anchor_to_graph(ner_entities, kb_ids_str)
                        except Exception as e:
                            logger.warning("Retrieve Graph RAG 失败（已忽略）: %s", e)
                            anchor_step.error_message = f"{type(e).__name__}: {e}"
                    anchor_step.step_output = {"tag_count": len(entity_tags)}

                # 混合检索
                # bm25 实际生效值：API enable_bm25 优先，未传则跟随 settings.bm25_enable
                # 这里写入 step_output 让 trace/analytics 能精确判定 BM25 是否真正参与
                settings = get_settings()
                bm25_effective = (
                    body.enable_bm25 if body.enable_bm25 is not None else settings.bm25_enable
                )
                with tracer.step(
                    "retrieve",
                    step_input={
                        "top_k": body.top_k,
                        "rerank": body.rerank,
                        "entity_tag_count": len(entity_tags),
                        "bm25_enabled": bm25_effective,
                    },
                ) as retrieve_step:
                    results = await hybrid_search(
                        query=body.query,
                        top_k=body.top_k,
                        entity_tags=entity_tags or None,
                        reranker_enable=body.rerank,
                        similarity_threshold=body.similarity_threshold,
                    )
                    retrieve_step.step_output = {
                        "hit_count": len(results),
                        "bm25_enabled": bm25_effective,
                    }

                total_retrieved = len(results)

                # 转换为 RetrieveChunkItem
                chunks: list[RetrieveChunkItem] = []
                for r in results:
                    chunks.append(
                        RetrieveChunkItem(
                            chunk_id=r.chunk_id,
                            content=r.content,
                            document_name=(r.metadata or {}).get("filename", r.document_id),
                            page_number=r.page_number,
                            heading_path=r.heading_path,
                            vector_score=r.vector_score,
                            bm25_score=r.bm25_score,
                            rrf_score=r.rrf_score,
                            rerank_score=r.rerank_score,
                            metadata=r.metadata,
                        )
                    )

                after_rerank = len(chunks)
                total_latency_ms = int((time.perf_counter() - start) * 1000)

                return RetrieveResponse(
                    chunks=chunks,
                    total_retrieved=total_retrieved,
                    after_rerank=after_rerank,
                    trace_id=tracer.trace_id,
                    total_latency_ms=total_latency_ms,
                )
            except Exception as e:
                logger.error("Retrieve 失败: %s", e, exc_info=True)
                total_latency_ms = int((time.perf_counter() - start) * 1000)
                return RetrieveResponse(
                    chunks=[],
                    total_retrieved=0,
                    after_rerank=0,
                    trace_id=tracer.trace_id,
                    total_latency_ms=total_latency_ms,
                )
    finally:
        reset_current_kb_ids(kb_ids_token)
