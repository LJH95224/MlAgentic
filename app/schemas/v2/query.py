"""Query 相关 Schema（UQA-01 / HRE-01 / HRE-02 / HRE-06）。"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

# query_rewrite 合法枚举值；非法值由 endpoint/resolve_options 入口处抛 BusinessError(40011)，
# 不在 Pydantic field_validator 中拦截——Pydantic 会把任何 validator 异常重新打包成
# ValidationError，最终走 PARAM_INVALID(40001) 而非 PRD §1127 要求的 40011。
VALID_QUERY_REWRITE = ("none", "hyde", "multi_query")


class QueryOptions(BaseModel):
    """查询选项（HRE-06 三层合并的最高优先级层）。

    所有字段都用 ``None`` 表示「未指定，跟随 KB.retrieval_config 或全局 settings」；
    显式传值才覆盖底层配置。``top_k`` 默认 None 让 resolve_options 决定。
    """

    top_k: int | None = Field(default=None, ge=1, le=50, description="返回结果数量")
    reranker_enable: bool | None = Field(default=None, description="是否启用 Reranker")
    bm25_enable: bool | None = Field(default=None, description="是否启用 BM25")
    stream: bool = Field(default=False, description="是否使用流式输出（SSE）")

    # 新增 ─────────────────────────────────────
    query_rewrite: str | None = Field(
        default=None,
        description="Query 改写策略：none / hyde / multi_query（HRE-01）",
    )
    enable_graph_rag: bool | None = Field(
        default=None,
        description="是否启用 Graph RAG 锚定（HRE-02）",
    )
    similarity_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Reranker 过滤阈值（HRE-05）",
    )
    enable_faithfulness_check: bool | None = Field(
        default=None,
        description="是否启用答案自检（CHC-04）；None 跟随配置，默认 False",
    )


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

    # Query 增强阶段的可观测信息（调试用，前端可选展示）─────────────
    rewritten_query: str | None = Field(
        default=None,
        description="HyDE 改写后的假设性答案（仅 hyde 策略下有值）",
    )
    sub_queries: list[str] | None = Field(
        default=None,
        description="multi_query 拆出的子查询列表（仅 multi_query 策略下有值）",
    )
    ner_entities: list[dict] | None = Field(
        default=None,
        description="Query NER 抽取的实体 [{'name': ..., 'type': ...}]",
    )
    graph_anchored_tags: list[str] | None = Field(
        default=None,
        description="图谱锚定后注入到 Milvus entity_tags 的标签列表",
    )

    # CHC-03 置信度评分 + CHC-04 答案自检 ─────────────────────
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="CHC-03 整体置信度（0~1），基于被引用 chunk 的 rerank 分加权 + 引用覆盖率 + 自检惩罚",
    )
    low_confidence_warning: str | None = Field(
        default=None,
        description="confidence < 0.5 时的预警文案（PRD §556）",
    )
    faithfulness_check: str | None = Field(
        default=None,
        description="CHC-04 自检状态：ok / skipped / disabled",
    )
    unverified_claims: list[dict] | None = Field(
        default=None,
        description="CHC-04 未被支撑的事实声明列表 [{claim, status, source_text}]",
    )
