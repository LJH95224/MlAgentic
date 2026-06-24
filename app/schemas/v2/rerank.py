"""UQA-04 Reranker 子接口 Schema（POST /api/v2/rerank）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RerankCandidate(BaseModel):
    """待精排的候选文本。"""

    id: str = Field(..., min_length=1, description="候选文本唯一标识")
    text: str = Field(..., min_length=1, description="候选文本内容")


class RerankRequest(BaseModel):
    """UQA-04 Reranker 请求。"""

    query: str = Field(..., min_length=1, max_length=2000, description="查询文本")
    candidates: list[RerankCandidate] = Field(
        ..., min_length=1,
        description="候选文本列表（至少 1 条）",
    )
    top_n: int = Field(default=5, ge=1, le=50, description="返回的最大数量")


class RerankResultItem(BaseModel):
    """精排结果中的单条。"""

    id: str = Field(description="候选文本标识（与请求中的 id 对应）")
    text: str = Field(default="", description="候选文本内容")
    rerank_score: float = Field(description="精排分数")


class RerankResponse(BaseModel):
    """UQA-04 Reranker 响应。"""

    results: list[RerankResultItem] = Field(
        default_factory=list,
        description="按 rerank_score 降序排列的结果列表",
    )
    total_latency_ms: int | None = None
