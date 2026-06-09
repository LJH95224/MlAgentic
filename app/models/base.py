"""Declarative Base 与通用的列 mixin。"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""

    pass


class UUIDMixin:
    """提供 UUID 主键。"""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键 UUID",
    )


class TimestampMixin:
    """提供 created_at 时间戳。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="记录创建时间",
    )


def utcnow() -> datetime:
    """返回当前 UTC 时间（aware）。"""
    return datetime.now(timezone.utc)