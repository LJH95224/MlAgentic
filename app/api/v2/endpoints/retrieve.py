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
    settings = get_settings()

    # KB contextvar
    kb_ids_token = set_current_kb_ids(body.kb_ids)
    try:
        # Graph RAG：NER + 锚定（与 /v2/query 同款，但简化为无条件跟随配置）
        entity_tags: list[str] = []
        if body.enable_graph_rag:
            try:
                ner_entities = await extract_query_entities(body.query)
                if ner_entities:
                    kb_ids_str = [str(k) for k in body.kb_ids] if body.kb_ids else None
                    entity_tags = await anchor_to_graph(ner_entities, kb_ids_str)
            except Exception as e:
                logger.warning("Retrieve Graph RAG 失败（已忽略）: %s", e)

        # 混合检索
        results = await hybrid_search(
            query=body.query,
            top_k=body.top_k,
            entity_tags=entity_tags or None,
            reranker_enable=body.rerank,
            similarity_threshold=body.similarity_threshold,
        )

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
                    rerank_score=r.score,
                    metadata=r.metadata,
                )
            )

        after_rerank = len(chunks)
        total_latency_ms = int((time.perf_counter() - start) * 1000)

        return RetrieveResponse(
            chunks=chunks,
            total_retrieved=total_retrieved,
            after_rerank=after_rerank,
            trace_id="",
            total_latency_ms=total_latency_ms,
        )
    except Exception as e:
        logger.error("Retrieve 失败: %s", e, exc_info=True)
        total_latency_ms = int((time.perf_counter() - start) * 1000)
        return RetrieveResponse(
            chunks=[],
            total_retrieved=0,
            after_rerank=0,
            trace_id="",
            total_latency_ms=total_latency_ms,
        )
    finally:
        reset_current_kb_ids(kb_ids_token)
