"""eval_tasks 表：V2.0 RAGAS 评估任务记录（EVA-01/02/03）。

每次评估任务对应一组测试问题 → RAG 系统 → 收集答案 → RAGAS 打分。

设计要点：
- 评估集（eval_dataset）以 JSONB 存储，内含 question / ground_truth / contexts
- 评估结果（eval_result）以 JSONB 存储，内含 RAGAS 各指标分数
- status 与 KbFile 同款枚举（pending / processing / completed / failed）
- T11 阶段才接通，T0 仅建表
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


# 评估任务状态
EVAL_STATUS_PENDING = "pending"
EVAL_STATUS_PROCESSING = "processing"
EVAL_STATUS_COMPLETED = "completed"
EVAL_STATUS_FAILED = "failed"
EVAL_STATUS_CHOICES = (
    EVAL_STATUS_PENDING,
    EVAL_STATUS_PROCESSING,
    EVAL_STATUS_COMPLETED,
    EVAL_STATUS_FAILED,
)


class EvalTask(Base):
    """RAGAS 评估任务记录（V2.0 EVA-01/02/03）。"""

    __tablename__ = "eval_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="评估任务 UUID",
    )

    kb_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
        comment="被评估的知识库 ID",
    )

    name: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        comment="评估任务名称（便于识别）",
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=EVAL_STATUS_PENDING,
        server_default=EVAL_STATUS_PENDING,
        index=True,
        comment="状态：pending / processing / completed / failed",
    )

    progress: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="评估进度 0~100",
    )

    # 评估集：[{question, ground_truth, contexts}, ...]
    eval_dataset: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="评估数据集（question / ground_truth / contexts）",
    )

    # 评估结果：{faithfulness: 0.85, answer_relevancy: 0.72, ...}
    eval_result: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="RAGAS 评估结果（各指标分数）",
    )

    # RAGAS 配置快照
    eval_config: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="评估配置快照（使用的 LLM / embeddings / metrics 列表等）",
    )

    question_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="评估集中的问题数量",
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        comment="失败时的错误摘要",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="完成时间",
    )

    def __repr__(self) -> str:
        return f"<EvalTask id={self.id} kb={self.kb_id} status={self.status}>"
