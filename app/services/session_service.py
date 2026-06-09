"""会话相关的业务逻辑。"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import ChatSession


async def create_session(db: AsyncSession) -> ChatSession:
    """创建一个新会话并落库（API-01）。"""
    session = ChatSession()
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def get_session(db: AsyncSession, session_id: uuid.UUID) -> ChatSession | None:
    """按 ID 获取会话；不存在返回 None。"""
    stmt = select(ChatSession).where(ChatSession.id == session_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()