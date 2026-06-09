"""pytest 全局夹具。

⚠️ 测试依赖说明
================
V1.0 的 ORM 直接使用了 PostgreSQL 专有类型（JSONB / UUID），因此集成测试
**必须**连接真实 PostgreSQL（或兼容服务，如 CockroachDB / Yugabyte）。

测试执行前请确保：
  1. PostgreSQL 已启动且可访问；
  2. 已创建专用测试库（建议 `tyagent_test`）；
  3. 环境变量 TEST_DATABASE_URL 已设置，例如：
       export TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/tyagent_test

若未设置 TEST_DATABASE_URL，所有依赖 DB 的测试会被 skip。
"""

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# 检测测试库是否可用
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
HAS_DB = bool(TEST_DATABASE_URL)

# 在 import app 之前重写 DATABASE_URL
if HAS_DB:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["APP_DEBUG"] = "false"


skip_without_db = pytest.mark.skipif(
    not HAS_DB,
    reason="需要 TEST_DATABASE_URL（指向一个可用的 PostgreSQL 测试库）",
)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """提供直连 FastAPI app 的 httpx 客户端，并驱动 lifespan（建表）。

    每个用例独立 lifespan，建/拆表互不污染。
    """
    if not HAS_DB:
        pytest.skip("需要 TEST_DATABASE_URL")

    # 必须延后 import，让 settings 读到新的 DATABASE_URL
    from app.db.session import engine
    from app.main import app
    from app.models.base import Base

    # 每个用例开始前 drop_all + create_all，确保干净
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            yield ac


@pytest.fixture(autouse=True)
def _reload_settings_cache():
    """每次测试清空 Settings 缓存，避免环境变量污染。"""
    from app.core.config import get_settings

    get_settings.cache_clear()