"""Neo4j Writer 行为单测（不连真 Neo4j，mock AsyncDriver）。

已有 [tests/test_kg_writer.py](test_kg_writer.py) 6 case 仅覆盖 Cypher 文本静态检查；
本模块补充：

- 5 个 async 公开函数的运行时行为（session/execute_write/tx.run 的调用与参数）
- database 透传到 driver.session(database=...)
- 单条 vs 批量入参的精确透传
- 批量 rows=[] 短路（不开 session）
- ``await result.single()`` 返回 None 时批量返 0 的兜底

mock 策略：复用 unittest.mock.AsyncMock 构造 driver→session→tx 的三层链，
精确断言 ``tx.run`` 的第一参数（Cypher 文本）与 keyword 参数（参数化变量）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import get_settings
from app.kg.writer import (
    BULK_LINK_CYPHER,
    BULK_UPSERT_ENTITIES_CYPHER,
    LINK_ENTITY_TO_CHUNK_CYPHER,
    UPSERT_DOCUMENT_CYPHER,
    UPSERT_ENTITY_CYPHER,
    bulk_link_entities_to_chunk,
    bulk_upsert_entities,
    link_entity_to_chunk,
    upsert_document,
    upsert_entity,
)


# ───────────────────────── 公共 fixture ─────────────────────────


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """每个 case 跑完清掉 get_settings LRU，避免污染其它测试模块。"""
    yield
    get_settings.cache_clear()


@pytest.fixture
def _set_neo4j_database(monkeypatch):
    """注入 settings.neo4j_database，便于断言 driver.session(database=...) 透传。"""

    def _apply(name: str = "tyagent"):
        settings = get_settings()
        monkeypatch.setattr(settings, "neo4j_database", name, raising=True)
        return name

    return _apply


class _MockDriver:
    """模拟 neo4j.AsyncDriver。

    - ``driver.session(database=...)`` 返回支持 async with 的上下文管理器
    - ``session.execute_write(tx_fn, *args, **kwargs)`` 调用 tx_fn(tx, *args, **kwargs)
    - ``tx.run(cypher, **params)`` 返回 result，``result.single()`` 返回预设 record

    精确记录所有调用，方便断言。
    """

    def __init__(self, single_record: dict | None = None):
        self.single_record = single_record
        # 记录 tx.run 的所有调用：[(cypher, kwargs), ...]
        self.run_calls: list[tuple[str, dict]] = []
        # 记录 driver.session(...) 的关键字
        self.session_kwargs: list[dict] = []

    def session(self, **kwargs):
        self.session_kwargs.append(kwargs)
        outer = self

        @asynccontextmanager
        async def _ctx():
            sess = _MockSession(outer)
            yield sess

        return _ctx()


class _MockSession:
    """模拟 neo4j AsyncSession。"""

    def __init__(self, driver: _MockDriver):
        self._driver = driver

    async def execute_write(self, tx_fn, *args, **kwargs):
        """模拟事务执行：构造一个 mock tx，把 tx.run 的调用记录到 driver."""
        tx = _MockTx(self._driver)
        return await tx_fn(tx, *args, **kwargs)


class _MockTx:
    """模拟 neo4j AsyncManagedTransaction。"""

    def __init__(self, driver: _MockDriver):
        self._driver = driver

    async def run(self, cypher: str, **params):
        self._driver.run_calls.append((cypher, params))
        result = MagicMock()
        result.single = AsyncMock(return_value=self._driver.single_record)
        return result


# ───────────────────────── upsert_document ─────────────────────────


class TestUpsertDocument:
    @pytest.mark.asyncio
    async def test_passes_args_to_cypher_params(self, _set_neo4j_database):
        """document_id / title / created_at 必须按参数化传给 tx.run。"""
        db_name = _set_neo4j_database("kb_db")
        driver = _MockDriver(single_record={"document_id": "doc-42"})

        returned = await upsert_document(
            driver, document_id="doc-42", title="台风分析", created_at="2026-06-23T10:00:00"
        )

        assert returned == "doc-42"
        # session 必须按 settings.neo4j_database 打开
        assert driver.session_kwargs == [{"database": db_name}]
        # tx.run 必须传完整的 cypher + 三个参数
        assert len(driver.run_calls) == 1
        cypher, params = driver.run_calls[0]
        assert cypher == UPSERT_DOCUMENT_CYPHER
        assert params == {
            "document_id": "doc-42",
            "title": "台风分析",
            "created_at": "2026-06-23T10:00:00",
        }

    @pytest.mark.asyncio
    async def test_created_at_none_passes_through(self, _set_neo4j_database):
        """created_at 默认 None 时仍参数化下发（coalesce 在 Cypher 端兜底）。"""
        _set_neo4j_database()
        driver = _MockDriver(single_record={"document_id": "doc-1"})

        await upsert_document(driver, document_id="doc-1", title="t")

        _, params = driver.run_calls[0]
        assert params["created_at"] is None


# ───────────────────────── upsert_entity ─────────────────────────


class TestUpsertEntity:
    @pytest.mark.asyncio
    async def test_returns_name_and_type_tuple(self, _set_neo4j_database):
        """upsert_entity 必须返回 (name, type) 二元组，便于上层验证 MERGE 结果。"""
        _set_neo4j_database()
        driver = _MockDriver(
            single_record={"name": "西北太平洋", "type": "LOCATION"}
        )

        result = await upsert_entity(
            driver,
            name="西北太平洋",
            entity_type="LOCATION",
            document_id="doc-1",
        )

        assert result == ("西北太平洋", "LOCATION")
        cypher, params = driver.run_calls[0]
        assert cypher == UPSERT_ENTITY_CYPHER
        # 关键：name 与 type 必须以独立参数传递（复合键幂等的前提）
        assert params == {
            "name": "西北太平洋",
            "type": "LOCATION",
            "document_id": "doc-1",
        }

    @pytest.mark.asyncio
    async def test_same_name_different_type_are_separate_params(
        self, _set_neo4j_database
    ):
        """关键契约：name 与 type 都参数化 — 同名不同类型可共存（"苹果" ORG vs OTHER）。

        通过两次独立调用断言：参数互不串扰，Cypher 用复合键区分。
        """
        _set_neo4j_database()
        driver = _MockDriver(
            single_record={"name": "苹果", "type": "ORG"}
        )

        await upsert_entity(driver, name="苹果", entity_type="ORG", document_id="d1")
        await upsert_entity(driver, name="苹果", entity_type="OTHER", document_id="d1")

        assert len(driver.run_calls) == 2
        # 两次 Cypher 文本完全相同（参数化），但 type 不同
        assert driver.run_calls[0][1]["type"] == "ORG"
        assert driver.run_calls[1][1]["type"] == "OTHER"


# ───────────────────────── link_entity_to_chunk ─────────────────────────


class TestLinkEntityToChunk:
    @pytest.mark.asyncio
    async def test_passes_chunk_id_as_int(self, _set_neo4j_database):
        """chunk_id 必须以 int 类型参数化（Milvus chunk_id 是 INT64）。"""
        _set_neo4j_database()
        driver = _MockDriver()

        await link_entity_to_chunk(
            driver,
            entity_name="台风",
            entity_type="OTHER",
            document_id="doc-1",
            chunk_id=12345678901234,
        )

        cypher, params = driver.run_calls[0]
        assert cypher == LINK_ENTITY_TO_CHUNK_CYPHER
        assert params == {
            "name": "台风",
            "type": "OTHER",
            "document_id": "doc-1",
            "chunk_id": 12345678901234,
        }
        # 类型校验：确保未被意外转 str
        assert isinstance(params["chunk_id"], int)

    @pytest.mark.asyncio
    async def test_link_returns_none(self, _set_neo4j_database):
        """link_entity_to_chunk 不返回值（行为副作用型）。"""
        _set_neo4j_database()
        driver = _MockDriver()

        result = await link_entity_to_chunk(
            driver,
            entity_name="X",
            entity_type="Y",
            document_id="d",
            chunk_id=1,
        )

        assert result is None


# ───────────────────────── bulk_upsert_entities ─────────────────────────


class TestBulkUpsertEntities:
    @pytest.mark.asyncio
    async def test_empty_rows_short_circuits(self, _set_neo4j_database):
        """rows=[] 必须立刻返 0，不能开 session（避免无效 round-trip）。"""
        _set_neo4j_database()
        driver = _MockDriver()

        n = await bulk_upsert_entities(driver, [])

        assert n == 0
        # 关键断言：连 session 都不应被打开
        assert driver.session_kwargs == []
        assert driver.run_calls == []

    @pytest.mark.asyncio
    async def test_passes_rows_as_single_kwarg(self, _set_neo4j_database):
        """所有 rows 必须以单一 $rows 参数下发（UNWIND 一次性处理）。"""
        _set_neo4j_database()
        driver = _MockDriver(single_record={"n": 3})
        rows = [
            {"name": "A", "type": "ORG", "document_id": "d1"},
            {"name": "B", "type": "LOCATION", "document_id": "d1"},
            {"name": "C", "type": "OTHER", "document_id": "d2"},
        ]

        n = await bulk_upsert_entities(driver, rows)

        assert n == 3
        # 全部 rows 必须在一次 tx.run 内下发
        assert len(driver.run_calls) == 1
        cypher, params = driver.run_calls[0]
        assert cypher == BULK_UPSERT_ENTITIES_CYPHER
        assert params == {"rows": rows}

    @pytest.mark.asyncio
    async def test_returns_zero_when_single_record_is_none(self, _set_neo4j_database):
        """``await result.single()`` 返 None 时（异常情形），批量函数兜底返 0 而非崩。"""
        _set_neo4j_database()
        driver = _MockDriver(single_record=None)

        n = await bulk_upsert_entities(
            driver, [{"name": "A", "type": "ORG", "document_id": "d"}]
        )

        assert n == 0


# ───────────────────────── bulk_link_entities_to_chunk ─────────────────────────


class TestBulkLinkEntitiesToChunk:
    @pytest.mark.asyncio
    async def test_empty_rows_short_circuits(self, _set_neo4j_database):
        """同 bulk_upsert：rows=[] 应早退，不开 session。"""
        _set_neo4j_database()
        driver = _MockDriver()

        n = await bulk_link_entities_to_chunk(driver, [])

        assert n == 0
        assert driver.session_kwargs == []

    @pytest.mark.asyncio
    async def test_passes_rows_with_chunk_id(self, _set_neo4j_database):
        """rows 中每条必须包含 chunk_id；批量原样透传。"""
        _set_neo4j_database()
        driver = _MockDriver(single_record={"n": 2})
        rows = [
            {"name": "A", "type": "ORG", "document_id": "d1", "chunk_id": 10},
            {"name": "B", "type": "ORG", "document_id": "d1", "chunk_id": 11},
        ]

        n = await bulk_link_entities_to_chunk(driver, rows)

        assert n == 2
        cypher, params = driver.run_calls[0]
        assert cypher == BULK_LINK_CYPHER
        assert params == {"rows": rows}


__all__: list[str] = []
