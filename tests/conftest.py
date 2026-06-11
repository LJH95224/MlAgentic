"""pytest 全局夹具。

V1.0 集成测试依赖真实 PostgreSQL（通过 TEST_DATABASE_URL）。
LLM 单元测试使用 mock，不依赖网络 / 环境变量。

V1.5 改造（2026-06-11）：
- 新增 `pg_client` fixture：跳过 lifespan 里的 Milvus / Neo4j 初始化，只接 PG ——
  SES-01~06 / chat_service 这类不依赖 LLM / Milvus / Neo4j 的集成测试改用这个，
  速度从 ~10s/case 降到 ~1s/case
- 原 `client` fixture 保留给真正需要 Milvus + Neo4j + LLM 的端到端测试（test_chat_stream）
- 不在全局切换 Windows asyncio 事件循环策略：subprocess 测试（script_runner）
  必须用 ProactorEventLoop，全局切到 SelectorEventLoop 会导致它们 NotImplementedError
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


# ────────────── 集成测试连接重试 ──────────────


async def _create_all_with_retry(engine, max_attempts: int = 2) -> None:
    """fixture setup 时 drop_all + create_all，带轻量重试。

    2026-06-11 起 PG 已搬到本地 docker-compose（127.0.0.1:5432），网络稳定。
    仍保留 2 次重试作为容器刚启动 / 偶发抖动的兜底，开发体验更顺。
    """
    import asyncio

    from app.models.base import Base

    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)
            return
        except (OSError, asyncio.TimeoutError) as e:
            last_err = e
            if attempt < max_attempts:
                import logging

                logging.getLogger(__name__).warning(
                    "PG 连接失败（第 %d/%d 次，等 1s 重试）: %r",
                    attempt,
                    max_attempts,
                    e,
                )
                await asyncio.sleep(1)
            try:
                await engine.dispose()
            except Exception:  # noqa: BLE001
                pass
    raise last_err  # type: ignore[misc]


# ────────────── client fixtures ──────────────


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """重型 client：跑 lifespan（含 Milvus + Neo4j 初始化）。

    适用于：真正需要端到端走 RAG/KG/LLM 的测试，如 test_chat_stream。
    其他纯 PG 集成测试请用 `pg_client` 节省时间。
    """
    if not HAS_DB:
        pytest.skip("需要 TEST_DATABASE_URL")

    from app.db.session import engine
    from app.main import app

    await _create_all_with_retry(engine)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            async with app.router.lifespan_context(app):
                yield ac
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def pg_client() -> AsyncIterator[AsyncClient]:
    """轻量 client：跳过 lifespan 里的 Milvus / Neo4j 初始化，只接 PG。

    适用：SES-01~06 / chat_service / kb_files 等纯 PG 业务集成测试。

    工作原理：用 monkeypatch 把 init_milvus / init_neo4j（及 close_*）替换成
    no-op，再走 lifespan_context —— 这样 PG 建表 + handler 注册仍然完成，
    但完全不会去连远程 Milvus / Neo4j，集成测试速度从 ~10s/case 降到 ~1s/case。

    用例结束时 engine.dispose() 强制释放连接池，避免下个用例复用旧 asyncpg 连接
    导致 "Event loop is closed" / "信号灯超时" 等跨 event loop 错误（Windows 上
    pytest-asyncio 每个用例 close 自己的 event loop，连接池里旧连接绑在前一个
    loop 上）。
    """
    if not HAS_DB:
        pytest.skip("需要 TEST_DATABASE_URL")

    from app.db.session import engine
    from app.main import app

    await _create_all_with_retry(engine)

    # monkeypatch 掉 Milvus / Neo4j 的 init / close（lifespan 里调它们）
    import app.main as main_mod

    async def _noop_async():
        return None

    def _noop_sync():
        return None

    original_init_milvus = main_mod.init_milvus
    original_close_milvus = main_mod.close_milvus
    original_init_neo4j = main_mod.init_neo4j
    original_close_neo4j = main_mod.close_neo4j

    main_mod.init_milvus = _noop_sync
    main_mod.close_milvus = _noop_sync
    main_mod.init_neo4j = _noop_async
    main_mod.close_neo4j = _noop_async

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            async with app.router.lifespan_context(app):
                yield ac
    finally:
        # 恢复原函数
        main_mod.init_milvus = original_init_milvus
        main_mod.close_milvus = original_close_milvus
        main_mod.init_neo4j = original_init_neo4j
        main_mod.close_neo4j = original_close_neo4j
        # 强制释放 asyncpg 连接池：pytest-asyncio 每个用例独立 event loop，
        # 不 dispose 的话下次用例会复用绑在旧 loop 上的连接 → 信号灯超时
        await engine.dispose()


@pytest_asyncio.fixture
async def kb_client() -> AsyncIterator[AsyncClient]:
    """KB 集成 client：真 PG + 真 Milvus，跳过 Neo4j。

    适用：S2 KB CRUD 集成测试（KB-01 真建 Collection、KB-05 真 drop Collection）。
    与 pg_client 的差异：保留 Milvus init/close，只 monkeypatch 掉 Neo4j。

    用例结束时除 engine.dispose() 外，还会清理本测产生的 KB Collection
    （以 `kb_` 开头的临时 Collection）—— 避免污染 Milvus 实例。
    """
    if not HAS_DB:
        pytest.skip("需要 TEST_DATABASE_URL")

    from app.db.session import engine
    from app.main import app

    await _create_all_with_retry(engine)

    # 只 monkeypatch Neo4j（S5 才接通），保留 Milvus
    import app.main as main_mod

    async def _noop_async():
        return None

    original_init_neo4j = main_mod.init_neo4j
    original_close_neo4j = main_mod.close_neo4j
    main_mod.init_neo4j = _noop_async
    main_mod.close_neo4j = _noop_async

    # 记录测试开始前已存在的 KB Collection（避免误删用户数据）
    pre_existing_kb_collections: set[str] = set()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            async with app.router.lifespan_context(app):
                # lifespan 已经 init_milvus，可拿 client 记录初始状态
                try:
                    from app.rag.milvus_client import get_milvus_client
                    from app.rag.naming import is_kb_collection_name

                    milvus_client = get_milvus_client()
                    for name in milvus_client.list_collections():
                        if is_kb_collection_name(name):
                            pre_existing_kb_collections.add(name)
                except Exception:  # noqa: BLE001
                    # Milvus 不可达时让 lifespan 抛错，不要在 fixture 里掩盖
                    pass

                yield ac

                # 用例结束后清理本测期间新增的 KB Collection
                try:
                    from app.rag.milvus_client import get_milvus_client
                    from app.rag.naming import is_kb_collection_name

                    milvus_client = get_milvus_client()
                    for name in milvus_client.list_collections():
                        if (
                            is_kb_collection_name(name)
                            and name not in pre_existing_kb_collections
                        ):
                            try:
                                milvus_client.drop_collection(name)
                            except Exception:  # noqa: BLE001
                                pass
                except Exception:  # noqa: BLE001
                    pass
    finally:
        main_mod.init_neo4j = original_init_neo4j
        main_mod.close_neo4j = original_close_neo4j
        await engine.dispose()


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