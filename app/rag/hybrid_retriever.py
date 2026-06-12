"""V2.0 混合检索引擎（HRE-03/04）：稠密向量 + BM25 + RRF 融合。

核心功能：
- 稠密向量检索（HNSW + COSINE）：语义相似度
- BM25 稀疏检索（SPARSE_INVERTED_INDEX + BM25）：精确词频匹配
- RRF（Reciprocal Rank Fusion）融合：两路结果合并重排

设计要点：
- 使用 Milvus 2.5+ 的 hybrid_search API 一次性查询双路
- BM25 查询直接传原始文本（不需要手动计算稀疏向量）
- RRF 参数 k=60（学术标准值，可通过 RRF_K 配置调整）
- 降级策略：bm25_enable=False 时退化为纯稠密向量检索
- 异常策略：hybrid_search 失败时降级为纯向量检索

被 V2 统一查询接口（T6 /v2/query）和 search_knowledge_base 工具调用。
"""

from __future__ import annotations

import logging
import uuid

from app.agent.context import get_current_kb_ids
from app.core.config import get_settings
from app.rag.embedding import aembed_texts
from app.rag.milvus_client import get_milvus_client
from app.rag.naming import build_kb_collection_name
from app.rag.retriever import _build_filter_expr, _format_hits, get_current_role

logger = logging.getLogger(__name__)


# ──────────────── 数据类 ────────────────


class HybridSearchResult:
    """混合检索单条结果。"""

    __slots__ = ("chunk_id", "content", "document_id", "score",
                 "entity_tags", "heading_path", "block_type",
                 "page_number", "metadata", "source_collection")

    def __init__(
        self,
        *,
        chunk_id: int | None = None,
        content: str = "",
        document_id: str = "",
        score: float = 0.0,
        entity_tags: list[str] | None = None,
        heading_path: list[str] | None = None,
        block_type: str = "",
        page_number: int | None = None,
        metadata: dict | None = None,
        source_collection: str = "",
    ):
        self.chunk_id = chunk_id
        self.content = content
        self.document_id = document_id
        self.score = score
        self.entity_tags = entity_tags or []
        self.heading_path = heading_path or []
        self.block_type = block_type
        self.page_number = page_number
        self.metadata = metadata or {}
        self.source_collection = source_collection


# ──────────────── 核心检索 ────────────────


async def hybrid_search(
    query: str,
    *,
    top_k: int = 5,
    doc_type: str | None = None,
    document_id: str | None = None,
    entity_tags: list[str] | None = None,
) -> list[HybridSearchResult]:
    """V2.0 混合检索：稠密向量 + BM25 + RRF 融合。

    Args:
        query: 检索自然语言查询
        top_k: 返回最相关结果数
        doc_type: 按文档类型过滤
        document_id: 限定到具体文档
        entity_tags: 限定召回的切片必须包含其中任一实体标签

    Returns:
        HybridSearchResult 列表，按融合分数降序
    """
    settings = get_settings()
    current_role = get_current_role()
    current_kb_ids = get_current_kb_ids()

    # 入参校验
    if top_k < 1:
        top_k = 5
    if top_k > 50:
        top_k = 50

    # 决定查询的 Collection 列表
    if current_kb_ids is None:
        target_collections = [settings.milvus_collection]
    elif len(current_kb_ids) == 0:
        # 明确"不查任何 KB"——在调用 Milvus 之前就返回
        logger.info("hybrid_search: kb_ids=[] 显式空，跳过检索")
        return []
    else:
        target_collections = [build_kb_collection_name(kb) for kb in current_kb_ids]

    client = get_milvus_client()

    # 文本 → 稠密向量
    vectors = await aembed_texts([query])
    query_vec = vectors[0]

    # 拼过滤表达式
    filter_expr = _build_filter_expr(doc_type, document_id, entity_tags, current_role)

    # 输出字段
    output_fields = [
        "chunk_id",
        "content",
        "document_id",
        "metadata",
        "entity_tags",
        "heading_path",
        "block_type",
        "page_number",
    ]

    logger.info(
        "hybrid_search: query=%r top_k=%d bm25=%s collections=%s",
        query[:60],
        top_k,
        settings.bm25_enable,
        target_collections,
    )

    # 多 Collection 检索 + 合并
    all_results: list[HybridSearchResult] = []

    for collection in target_collections:
        if not client.has_collection(collection):
            logger.warning("hybrid_search: collection=%s 不存在，跳过", collection)
            continue

        try:
            results = _search_single_collection(
                client=client,
                collection=collection,
                query_text=query,
                query_vec=query_vec,
                top_k=top_k,
                filter_expr=filter_expr,
                output_fields=output_fields,
                bm25_enable=settings.bm25_enable,
                rrf_k=settings.rrf_k,
            )
            all_results.extend(results)
        except Exception as e:
            logger.warning(
                "hybrid_search: collection=%s 检索失败，尝试降级为纯向量检索: %s",
                collection,
                e,
            )
            # 降级：纯向量检索
            try:
                results = _fallback_dense_search(
                    client=client,
                    collection=collection,
                    query_vec=query_vec,
                    top_k=top_k,
                    filter_expr=filter_expr,
                    output_fields=output_fields,
                )
                all_results.extend(results)
            except Exception as inner_e:
                logger.warning(
                    "hybrid_search: 降级检索也失败 collection=%s: %s",
                    collection,
                    inner_e,
                )

    # 合并重排（按 score 降序）
    all_results.sort(key=lambda r: r.score, reverse=True)
    merged = all_results[:top_k]

    logger.info(
        "hybrid_search: 跨 %d collection 合并后命中 %d 条（top_k=%d）",
        len(target_collections),
        len(merged),
        top_k,
    )

    return merged


def _search_single_collection(
    *,
    client,
    collection: str,
    query_text: str,
    query_vec: list[float],
    top_k: int,
    filter_expr: str,
    output_fields: list[str],
    bm25_enable: bool,
    rrf_k: int,
) -> list[HybridSearchResult]:
    """在单个 Collection 上执行混合检索。"""
    from pymilvus import AnnSearchRequest, RRFRanker

    if bm25_enable:
        # ── 双路混合检索：稠密向量 + BM25 ──
        # 稠密向量检索请求
        dense_req = AnnSearchRequest(
            data=[query_vec],
            anns_field="vector",
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=top_k,
            expr=filter_expr,
        )

        # BM25 稀疏检索请求：直接传原始文本，Milvus 自动做分词+BM25 计算
        sparse_req = AnnSearchRequest(
            data=[query_text],
            anns_field="sparse_vector",
            param={"metric_type": "BM25"},
            limit=top_k,
            expr=filter_expr,
        )

        # hybrid_search + RRF 融合
        raw_results = client.hybrid_search(
            collection_name=collection,
            reqs=[dense_req, sparse_req],
            rerank=RRFRanker(k=rrf_k),
            limit=top_k,
            output_fields=output_fields,
        )
    else:
        # ── BM25 禁用时退化为纯向量检索 ──
        raw_results = client.search(
            collection_name=collection,
            data=[query_vec],
            anns_field="vector",
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=top_k,
            expr=filter_expr,
            output_fields=output_fields,
        )

    return _parse_search_results(raw_results, collection)


def _fallback_dense_search(
    *,
    client,
    collection: str,
    query_vec: list[float],
    top_k: int,
    filter_expr: str,
    output_fields: list[str],
) -> list[HybridSearchResult]:
    """降级为纯稠密向量检索（hybrid_search 失败时调用）。"""
    raw_results = client.search(
        collection_name=collection,
        data=[query_vec],
        anns_field="vector",
        param={"metric_type": "COSINE", "params": {"ef": 64}},
        limit=top_k,
        expr=filter_expr,
        output_fields=output_fields,
    )
    return _parse_search_results(raw_results, collection)


def _parse_search_results(
    raw_results: list, collection: str
) -> list[HybridSearchResult]:
    """解析 Milvus search/hybrid_search 返回结果为 HybridSearchResult 列表。"""
    results: list[HybridSearchResult] = []

    if not raw_results:
        return results

    # raw_results 结构：list[list[dict]]，外层每个 query 一个结果列表
    hits = raw_results[0] if raw_results else []

    for hit in hits:
        if not isinstance(hit, dict):
            continue
        entity = hit.get("entity", {})
        distance = hit.get("distance", 0.0)

        results.append(
            HybridSearchResult(
                chunk_id=entity.get("chunk_id"),
                content=entity.get("content", ""),
                document_id=entity.get("document_id", ""),
                score=distance,
                entity_tags=entity.get("entity_tags") or [],
                heading_path=entity.get("heading_path") or [],
                block_type=entity.get("block_type", ""),
                page_number=entity.get("page_number"),
                metadata=entity.get("metadata") or {},
                source_collection=collection,
            )
        )

    return results


def format_hybrid_results(results: list[HybridSearchResult]) -> str:
    """把混合检索结果格式化为对 LLM 友好的字符串（含 V2 结构信息）。"""
    if not results:
        return "（检索无结果。可尝试换关键词或放宽过滤后重试。）"

    lines = []
    for i, r in enumerate(results, start=1):
        tags_str = f", tags=[{','.join(r.entity_tags)}]" if r.entity_tags else ""
        heading_str = ""
        if r.heading_path:
            heading_str = f", heading={' > '.join(r.heading_path)}"
        page_str = f", p{r.page_number}" if r.page_number is not None else ""

        lines.append(
            f"[{i}] (score={r.score:.3f}, doc={r.document_id}{tags_str}{heading_str}{page_str})\n{r.content}"
        )

    return "\n\n".join(lines)


__all__ = [
    "HybridSearchResult",
    "hybrid_search",
    "format_hybrid_results",
]
