"""FastAPI 全局依赖注入。

集中提供导出，避免 endpoint 文件直接 import 底层模块。
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session


async def get_db() -> AsyncIterator[AsyncSession]:
    """注入一个异步数据库 Session（每个请求独立）。"""
    async for session in get_db_session():
        yield session


# 类型别名：endpoint 中可写 `db: DBSessionDep` 直接获得 AsyncSession
DBSessionDep = Annotated[AsyncSession, Depends(get_db)]