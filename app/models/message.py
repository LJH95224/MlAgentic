"""chat_messages 表：消息上下文。"""

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class ChatMessage(TimestampMixin, Base):
    """消息上下文表（PRD §4.2）。

    记录会话中的每一条消息。role 字段限定为 system / user / assistant / tool。
    tool_calls 字段以 JSONB 存储模型调用的工具名称与入参。
    """

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="消息唯一标识",
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属会话 ID",
    )

    role: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="角色：system / user / assistant / tool",
    )

    content: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None, comment="消息内容（tool 角色可为空）"
    )

    tool_calls: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        default=None,
        comment="model 调用的工具名称与参数（JSON 列表）",
    )

    session = relationship("ChatSession", back_populates="messages")

    def __repr__(self) -> str:
        return f"<ChatMessage id={self.id} role={self.role}>"