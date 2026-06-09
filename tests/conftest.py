"""pytest 全局夹具。

V1.0 集成测试依赖真实 PostgreSQL（通过 TEST_DATABASE_URL）。
LLM 单元测试使用 mock，不依赖网络 / 环境变量。
"""

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# ────────────── 集成测试 DB 相关 ──────────────

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
HAS_DB = bool(TEST_DATABASE_URL)

if HAS_DB:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["APP_DEBUG"] = "false"


skip_without_db = pytest.mark.skipif(
    not HAS_DB,
    reason="需要 TEST_DATABASE_URL（指向一个可用的 PostgreSQL 测试库）",
)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """提供直连 FastAPI app 的 httpx 客户端，并驱动 lifespan（建表）。"""
    if not HAS_DB:
        pytest.skip("需要 TEST_DATABASE_URL")

    from app.db.session import engine
    from app.main import app
    from app.models.base import Base

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


# ────────────── LLM 测试相关辅助 ──────────────


@pytest.fixture
def mock_env_vars():
    """在测试中临时设置 LLM 环境变量并重新加载 Settings。

    用法：
        def test_foo(mock_env_vars):
            mock_env_vars(LITELLM_MODEL="deepseek/deepseek-chat", ...)
    """
    from app.core.config import get_settings

    def _apply(**kwargs):
        for k, v in kwargs.items():
            os.environ[k] = str(v) if v is not None else ""
        get_settings.cache_clear()

    yield _apply

    # 清理：恢复默认值
    keys_to_clear = [k for k in os.environ if k.startswith("LITELLM_")]
    for k in keys_to_clear:
        os.environ.pop(k, None)
    get_settings.cache_clear()