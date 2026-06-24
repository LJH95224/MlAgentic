"""UQA-02 纯检索子接口 Schema（POST /api/v2/retrieve）。"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class RetrieveRequest(BaseModel):
    """UQA-02 纯检索请求。

    只执行检索，不调用 LLM。支持与 /v2/query 相同的检索参数。
    """

    query: str = Field(..., min_length=1, max_length=2000, description="检索查询文本")
    kb_ids: list[uuid.UUID] | None = Field(default=None, description="限定知识库列表")
    top_k: int = Field(default=5, ge=1, le=50, description="返回结果数量")
    enable_graph_rag: bool | None = Field(default=None, description="是否启用 Graph RAG 锚定")
    enable_bm25: bool | None = Field(default=None, description="是否启用 BM25")
    rerank: bool = Field(default=True, description="是否启用 Reranker 精排")
    similarity_threshold: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="Reranker 过滤阈值",
    )


class RetrieveChunkItem(BaseModel):
    """检索返回的单条 Chunk，包含所有分数字段。"""

    chunk_id: int | None = None
    content: str = ""
    document_name: str = ""
    page_number: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    vector_score: float | None = Field(default=None, description="稠密向量检索分数")
    bm25_score: float | None = Field(default=None, description="BM25 稀疏检索分数")
    rrf_score: float | None = Field(default=None, description="RRF 融合分数")
    rerank_score: float | None = Field(default=None, description="Reranker 精排分数")
    metadata: dict | None = None


class RetrieveResponse(BaseModel):
    """UQA-02 纯检索响应。"""

    chunks: list[RetrieveChunkItem] = Field(default_factory=list)
    total_retrieved: int = Field(default=0, description="Rerank 前检索总命中数")
    after_rerank: int = Field(default=0, description="Rerank 后保留数")
    trace_id: str | None = None
    total_latency_ms: int | None = None
