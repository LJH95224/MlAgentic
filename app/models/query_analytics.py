"""query_analytics 表：V2.0 聚合统计快照（OBS-03）。

每次 /v2/query 调用结束时同步写一行汇总。
analytics 端点对该表做 SQL 聚合，无需扫描 agent_traces 的 JSONB 字段。

设计要点：
- 工具使用率用 bool 列 + AVG 聚合：AVG(graph_rag_triggered) = 触发率
- low_confidence 冗余存储 bool：避免聚合时浮点比较
- Token 数据简化为 total_tokens（Tracer 只记录 token_count 总数）
- react_steps = 该 trace 的步骤数
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class QueryAnalytics(Base):
    """查询聚合统计快照（V2.0 OBS-03）。"""

    __tablename__ = "query_analytics"

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
        comment="关联的 trace_id",
    )

    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="关联会话 ID",
    )

    kb_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="关联知识库 ID",
    )

    # 延迟
    total_latency_ms: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="总耗时（毫秒）",
    )

    # 置信度
    confidence: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="CHC-03 置信度 [0, 1]",
    )

    low_confidence: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="confidence < 0.5 标记",
    )

    # 工具使用（bool 标记，聚合时 AVG 即为触发率）
    graph_rag_triggered: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="Graph RAG 是否触发",
    )

    bm25_contributed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="BM25 是否参与检索",
    )

    faithfulness_check_triggered: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="答案自检是否触发",
    )

    # Token 消耗
    total_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="总 token 消耗",
    )

    # ReAct 步骤数
    react_steps: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="该 trace 的步骤数",
    )

    # 错误
    has_error: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="任一步骤有 error_message",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="记录创建时间",
    )

    def __repr__(self) -> str:
        return (
            f"<QueryAnalytics trace_id={self.trace_id!r} "
            f"latency={self.total_latency_ms}ms confidence={self.confidence}>"
        )
