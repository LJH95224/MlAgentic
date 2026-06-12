"""V2.0 Query 相关 Schema（UQA-01）。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class QueryOptions(BaseModel):
    """查询选项。"""

    top_k: int = Field(default=5, ge=1, le=50, description="返回结果数量")
    reranker_enable: bool | None = Field(default=None, description="是否启用 Reranker（None 表示跟随配置）")
    bm25_enable: bool | None = Field(default=None, description="是否启用 BM25（None 表示跟随配置）")
    stream: bool = Field(default=False, description="是否使用流式输出（SSE）")


class QueryRequest(BaseModel):
    """V2 统一查询请求。"""

    query: str = Field(..., min_length=1, max_length=2000, description="查询文本")
    session_id: uuid.UUID | None = Field(default=None, description="关联会话 ID")
    kb_ids: list[uuid.UUID] | None = Field(default=None, description="限定知识库列表")
    options: QueryOptions = Field(default_factory=QueryOptions, description="查询选项")


class CitationItem(BaseModel):
    """单条引用项。"""

    chunk_id: int | None = None
    document_name: str = ""
    page_number: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    snippet: str = ""
    rerank_score: float | None = None


class QueryResponse(BaseModel):
    """V2 统一查询响应（非流式）。"""

    answer: str
    source_citations: list[CitationItem] = Field(default_factory=list)
    trace_id: str | None = None
    total_latency_ms: int | None = None
