"""SQLAlchemy 2.0 异步引擎与 Session 工厂。

V1.0 仅使用一个全局 engine。生命周期由 FastAPI lifespan 控制：
- 应用启动时：create_all（V1.0 阶段直接建表，未引入 alembic）。
- 应用关闭时：engine.dispose()。

V1.5 改造（测试环境稳定性）：
- 检测到 TEST_DATABASE_URL（即跑 pytest 集成测试）时，自动用 NullPool 替代默认池
- 原因：pytest-asyncio 每个用例独立 event loop，连接池里残留的 asyncpg 连接绑在
  前一个 loop 上，下个用例复用 → "Event loop is closed" / WinError 121 信号灯超时；
  fixture 末尾的 engine.dispose() 只能管"正常退出"，setup 中途异常就跳过 dispose
- NullPool：每次操作新建连接，操作结束立即断；不复用 → 不可能跨 loop
- 代价：每次 DB 操作多 50~200ms 握手；生产路径完全不受影响（只在 TEST_DATABASE_URL 存在时启用）
"""

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

_settings = get_settings()

# asyncpg connect_args：
# - ssl=False 强制关闭 SSL 探测。
#   asyncpg 默认 ssl='prefer'，在 Windows 上 SSL 握手偶发 [WinError 121]
#   信号灯超时，破坏开发体验。如果服务端要求 SSL，可改成 'require' 或自定义
#   SSL Context。本项目当前 PG 实例未配置 SSL，所以直接关闭最稳。
_connect_args = {"ssl": False} if "+asyncpg" in _settings.database_url else {}

# 测试环境强制 NullPool（见模块 docstring 第二段）
_in_test_mode = bool(os.getenv("TEST_DATABASE_URL"))
_engine_kwargs: dict = {
    "echo": False,
    "future": True,
    "connect_args": _connect_args,
}
if _in_test_mode:
    _engine_kwargs["poolclass"] = NullPool
else:
    _engine_kwargs["pool_pre_ping"] = True

engine: AsyncEngine = create_async_engine(
    _settings.database_url,
    **_engine_kwargs,
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
