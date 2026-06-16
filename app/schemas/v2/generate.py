"""V2.0 UQA-03 纯生成子接口 Schema（POST /api/v2/generate）。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.v2.query import CitationItem


class ContextChunk(BaseModel):
    """开发者传入的自定义上下文块。"""

    chunk_id: str = Field(..., min_length=1, description="上下文块唯一标识")
    content: str = Field(..., min_length=1, description="上下文文本内容")
    source_label: str = Field(
        default="",
        description="来源标签（如 '采购合同_2024.pdf P3'），用于 Citation 映射",
    )


class GenerateOptions(BaseModel):
    """生成选项。"""

    stream: bool = Field(default=False, description="是否流式输出（暂不支持，预留）")
    enable_citation: bool = Field(default=True, description="是否启用 Citation 溯源")
    enable_faithfulness_check: bool = Field(default=False, description="是否启用答案自检")


class GenerateRequest(BaseModel):
    """UQA-03 纯生成请求。

    接受自定义 context_chunks，跳过检索，直接调 LLM 生成 + 溯源 + 自检。
    """

    query: str = Field(..., min_length=1, max_length=2000, description="查询文本")
    context_chunks: list[ContextChunk] = Field(
        ..., min_length=1,
        description="自定义上下文块列表（至少 1 条）",
    )
    options: GenerateOptions = Field(default_factory=GenerateOptions, description="生成选项")


class GenerateResponse(BaseModel):
    """UQA-03 纯生成响应。"""

    answer: str
    source_citations: list[CitationItem] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    low_confidence_warning: str | None = None
    faithfulness_check: str | None = Field(
        default=None,
        description="自检状态：ok / skipped / disabled",
    )
    unverified_claims: list[dict] | None = None
    trace_id: str | None = None
    total_latency_ms: int | None = None
