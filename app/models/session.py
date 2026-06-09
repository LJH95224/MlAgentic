"""chat_sessions 表：会话。"""

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDMixin, TimestampMixin


class ChatSession(UUIDMixin, TimestampMixin, Base):
    """会话表（PRD §4.1）。

    存储对话上下文，metadata 字段（JSONB）预留用于存储会话偏好、地理范围等。
    """

    __tablename__ = "chat_sessions"

    # metadata 是 SQLAlchemy Base 预留属性，用 db_metadata 避让
    db_metadata: Mapped[dict | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
        default=None,
        comment="预留元数据（会话偏好、地理范围等）",
    )

    messages = relationship(
        "ChatMessage",
        back_populates="session",
        order_by="ChatMessage.created_at",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<ChatSession id={self.id}>"