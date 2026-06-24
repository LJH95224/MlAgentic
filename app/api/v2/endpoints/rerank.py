"""UQA-04 Reranker 子接口 POST /api/v2/rerank。

接受 Query + 候选文本列表，返回精排后的结果。
允许开发者将 Reranker 能力独立使用。
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter

from app.rag.reranker import get_reranker
from app.schemas.v2.rerank import RerankRequest, RerankResponse, RerankResultItem

logger = logging.getLogger(__name__)

router = APIRouter(tags=["V2 分层子接口"])


@router.post("/rerank", response_model=RerankResponse)
async def v2_rerank(body: RerankRequest) -> RerankResponse:
    """UQA-04 独立精排：query + candidates → 按 rerank_score 降序。"""
    start = time.perf_counter()

    # 构造 chunks dict 列表（与 hybrid_retriever → reranker 接口对齐）
    chunks = [{"content": c.text} for c in body.candidates]

    # id → index 映射（reranker 返回 index，需要映射回 id）
    id_list = [c.id for c in body.candidates]
    text_list = [c.text for c in body.candidates]

    try:
        reranker = get_reranker()
        rerank_results = await reranker.rerank(body.query, chunks, top_k=body.top_n)

        # RerankResult.index → 映射回 candidate id
        items: list[RerankResultItem] = []
        for rr in rerank_results:
            idx = rr.index
            if idx < len(id_list):
                items.append(
                    RerankResultItem(
                        id=id_list[idx],
                        text=text_list[idx],
                        rerank_score=rr.relevance_score,
                    )
                )

        # 按 rerank_score 降序（reranker 内部已排，这里保险再排一次）
        items.sort(key=lambda x: x.rerank_score, reverse=True)

    except Exception as e:  # noqa: BLE001
        # 降级：返回原顺序，分数标 0
        logger.warning("Rerank 端点降级: %s", e)
        items = [
            RerankResultItem(id=id_list[i], text=text_list[i], rerank_score=0.0)
            for i in range(min(body.top_n, len(id_list)))
        ]

    total_latency_ms = int((time.perf_counter() - start) * 1000)
    return RerankResponse(results=items, total_latency_ms=total_latency_ms)
