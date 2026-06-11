"""chat_sessions 表：会话。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class ChatSession(UUIDMixin, TimestampMixin, Base):
    """会话表（V1.0 §4.1 → V1.5 §5.1 扩展）。

    V1.0 字段：id / created_at / metadata
    V1.5 新增：title / summary / summarized_at / updated_at / message_count
    """

    __tablename__ = "chat_sessions"

    # ------- V1.0 既有字段 -------
    # metadata 是 SQLAlchemy Base 预留属性，用 db_metadata 避让
    db_metadata: Mapped[dict | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
        default=None,
        comment="预留元数据（会话偏好、地理范围等）",
    )

    # ------- V1.5 新增字段（PRD §5.1） -------
    title: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        default=None,
        comment="会话标题，最长 100 字；首条消息后由异步任务自动生成（SES-07）",
    )

    summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        comment="会话摘要，由 POST /sessions/{id}/summarize 主动触发生成（SES-08）",
    )

    summarized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="最近一次摘要生成时间",
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="最近一次消息写入时自动更新（SES-02 列表按此倒序）",
    )

    message_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="冗余计数字段，提升列表查询性能（写消息时 +1）",
    )

    messages = relationship(
        "ChatMessage",
        back_populates="session",
        order_by="ChatMessage.created_at",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<ChatSession id={self.id} title={self.title!r}>"