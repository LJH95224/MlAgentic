"""SQLAlchemy 2.0 异步引擎与 Session 工厂。

V1.0 仅使用一个全局 engine。生命周期由 FastAPI lifespan 控制：
- 应用启动时：create_all（V1.0 阶段直接建表，未引入 alembic）。
- 应用关闭时：engine.dispose()。
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_settings = get_settings()

# asyncpg connect_args：
# - ssl=False 强制关闭 SSL 探测。
#   asyncpg 默认 ssl='prefer'，在 Windows 上 SSL 握手偶发 [WinError 121]
#   信号灯超时，破坏开发体验。如果服务端要求 SSL，可改成 'require' 或自定义
#   SSL Context。本项目当前 PG 实例未配置 SSL，所以直接关闭最稳。
_connect_args = {"ssl": False} if "+asyncpg" in _settings.database_url else {}

engine: AsyncEngine = create_async_engine(
    _settings.database_url,
    echo=False,
    pool_pre_ping=True,
    future=True,
    connect_args=_connect_args,
)

# expire_on_commit=False：避免 commit 后 ORM 对象属性被过期
# （在异步场景下访问过期属性会触发隐式 IO，体验很糟糕）
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：按请求维度提供一个 AsyncSession，并在请求结束时关闭。"""
    async with AsyncSessionLocal() as session:
        yield session
