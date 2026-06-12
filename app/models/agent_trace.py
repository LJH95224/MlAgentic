"""agent_traces 表：V2.0 可观测性 Trace 记录（OBS-01）。

每次 /v2/query 调用产生一条 trace，内部各步骤（解析/检索/重排/生成/引用解析等）
各产生一条子记录。通过 trace_id 串联完整推理链路。

设计要点：
- trace_id 是逻辑主键（非 PG 自增），由 Tracer 在入口生成，贯穿全链路
- step_type 标识步骤类型（如 parse / retrieve / rerank / generate / citation_parse）
- total_latency_ms 仅在 trace 根记录（parent_step=NULL）上填写
- 查询维度：session_id（会话维度）/ trace_id（单次调用维度）/ kb_id（知识库维度）
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AgentTrace(Base):
    """Agent 推理链路追踪记录（V2.0 OBS-01）。"""

    __tablename__ = "agent_traces"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="记录主键",
    )

    trace_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="逻辑 trace ID，贯穿一次 /v2/query 调用的所有步骤",
    )

    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="关联会话 ID（非 v2/query 调用时可能为空）",
    )

    kb_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="关联知识库 ID（单 KB 查询时填写）",
    )

    # 步骤信息
    step_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="步骤类型：parse / retrieve / rerank / generate / citation_parse / ...",
    )

    parent_step: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="父步骤 step_type（根步骤为 NULL）",
    )

    # 计时
    step_latency_ms: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="本步骤耗时（毫秒）",
    )

    total_latency_ms: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="整条 trace 总耗时（仅根步骤填写）",
    )

    # 输入输出快照
    step_input: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="步骤输入（query / top_k / filter 等快照）",
    )

    step_output: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="步骤输出（chunk 数 / token 数 / 引用数等快照）",
    )

    # 元数据
    model_name: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="步骤使用的 LLM 模型名（如有）",
    )

    token_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="步骤消耗的 token 数（如有 LLM 调用）",
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="步骤失败时的错误摘要",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="记录创建时间",
    )

    def __repr__(self) -> str:
        return (
            f"<AgentTrace trace_id={self.trace_id!r} "
            f"step={self.step_type!r} latency={self.step_latency_ms}ms>"
        )
