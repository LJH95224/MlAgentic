"""RAGAS 评估相关 Schema（EVA-01/02/03）。

请求/响应结构按 PRD §777-863 对齐：
- POST /api/v2/knowledge-bases/{kb_id}/evaluate
- GET  /api/v2/knowledge-bases/{kb_id}/evaluations/{eval_task_id}
- GET  /api/v2/knowledge-bases/{kb_id}/evaluations
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# ──────────────── 请求体 ────────────────


class EvalQAItem(BaseModel):
    """评估集中的单条 QA。"""

    question: str = Field(..., min_length=1, max_length=2000, description="评估问题")
    ground_truth: str = Field(..., min_length=1, max_length=4000, description="标准答案")


class EvalRetrievalOptions(BaseModel):
    """评估时使用的检索参数（QueryOptions 的子集，避免循环依赖）。

    所有字段为可选；评估期把这份配置整段写进 EvalTask.eval_config 做参数快照。
    """

    top_k: int | None = Field(default=None, ge=1, le=50, description="返回结果数量")
    enable_graph_rag: bool | None = Field(default=None, description="是否启用 Graph RAG 锚定")
    reranker_enable: bool | None = Field(default=None, description="是否启用 Reranker")
    bm25_enable: bool | None = Field(default=None, description="是否启用 BM25")
    query_rewrite: str | None = Field(default=None, description="Query 改写策略（none/hyde/multi_query）")
    similarity_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class EvalCreateRequest(BaseModel):
    """EVA-01 创建评估请求体。"""

    eval_set: list[EvalQAItem] = Field(..., min_length=0, description="评估集（QA 对列表）")
    retrieval_options: EvalRetrievalOptions = Field(
        default_factory=EvalRetrievalOptions,
        description="评估时的检索参数（不传走 settings 默认）",
    )
    name: str | None = Field(default=None, max_length=256, description="评估任务名称（便于识别）")


# ──────────────── 响应体 ────────────────


class EvalCreateResponse(BaseModel):
    """EVA-01 创建评估响应体。"""

    eval_task_id: uuid.UUID
    status: str = Field(default="pending", description="任务初始状态")


class EvalSummary(BaseModel):
    """RAGAS 4 项核心指标的汇总（PRD §817-822）。

    每项指标范围 [0, 1]；overall_score 为四项算术均值。
    单题或整批评估失败时各项可为 None。
    """

    faithfulness: float | None = Field(default=None, ge=0.0, le=1.0)
    answer_relevancy: float | None = Field(default=None, ge=0.0, le=1.0)
    context_precision: float | None = Field(default=None, ge=0.0, le=1.0)
    context_recall: float | None = Field(default=None, ge=0.0, le=1.0)
    overall_score: float | None = Field(default=None, ge=0.0, le=1.0)


class EvalDetailItem(BaseModel):
    """每道题的详细评估结果（PRD §839-848）。"""

    question: str
    ground_truth: str
    answer: str = ""
    contexts: list[str] = Field(default_factory=list, description="检索到的 chunk 文本列表")
    faithfulness: float | None = None
    answer_relevancy: float | None = None
    context_precision: float | None = None
    context_recall: float | None = None
    error: str | None = Field(default=None, description="单题失败时的简要错误信息")


class EvalDetailResponse(BaseModel):
    """EVA-02 评估结果查询响应体。"""

    eval_task_id: uuid.UUID
    kb_id: uuid.UUID
    name: str | None = None
    status: str = Field(description="pending / processing / completed / failed")
    progress: int = Field(default=0, ge=0, le=100)
    question_count: int = 0
    summary: EvalSummary | None = None
    details: list[EvalDetailItem] | None = None
    retrieval_options: dict | None = Field(
        default=None,
        description="评估时使用的检索参数快照（EvalTask.eval_config）",
    )
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class EvalListItem(BaseModel):
    """EVA-03 列表中的单条记录（不含每题详情）。"""

    eval_task_id: uuid.UUID
    name: str | None = None
    status: str
    progress: int = 0
    question_count: int = 0
    summary: EvalSummary | None = None
    retrieval_options: dict | None = None
    created_at: datetime
    completed_at: datetime | None = None


class EvalListResponse(BaseModel):
    """EVA-03 评估历史列表响应体。"""

    items: list[EvalListItem]
    total: int
    page: int = 1
    page_size: int = 20
