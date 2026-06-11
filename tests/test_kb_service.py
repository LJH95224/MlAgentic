"""V1.5 KB-01~05 service 层单测（mock DB + mock Milvus，CI 友好）。

覆盖 endpoint 测无法验的"service 内部协调"逻辑：
- KB-01 create_kb：先建 Milvus 再写 PG；任一失败回滚
- KB-04 update_kb：name 改不改的查重逻辑
- KB-05 delete_kb：严格按 Milvus → PG → Neo4j 顺序；Milvus 失败不动 PG；
  PG 失败时 Milvus 已删，记录日志后仍抛错

真实业务规则（PG 唯一索引并发场景 / Milvus 真实 collection 创建）走集成测试。
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api import error_codes
from app.api.exceptions import BusinessError
from app.models.knowledge_base import KnowledgeBase
from app.services import kb_service


def _make_db_mock(*, name_exists: bool = False):
    """造一个 AsyncSession mock；name 查重默认不存在。"""
    db = MagicMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.refresh = AsyncMock()

    # _kb_name_exists 内部跑 .execute(select).first()
    # 这里让 db.execute 返回一个 result，result.first() 按需返回 None / tuple
    result = MagicMock()
    result.first.return_value = (uuid.uuid4(),) if name_exists else None
    result.scalar_one_or_none.return_value = None
    result.scalar_one.return_value = 0
    result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result)
    return db


# ──────────────── KB-01 create_kb ────────────────


@pytest.fixture
def patched_milvus(monkeypatch):
    """同时 mock create_kb_collection / drop_kb_collection（覆盖 _safe_rollback_milvus）。"""
    create_mock = MagicMock(return_value="kb_test_collection")
    drop_mock = MagicMock(return_value=True)
    monkeypatch.setattr(kb_service, "create_kb_collection", create_mock)
    monkeypatch.setattr(kb_service, "drop_kb_collection", drop_mock)
    yield create_mock, drop_mock


async def test_create_kb_happy_path(patched_milvus, monkeypatch):
    create_mock, drop_mock = patched_milvus
    db = _make_db_mock(name_exists=False)

    kb = await kb_service.create_kb(
        db, name="法律库", embedding_dim=4096, chunk_size=512, chunk_overlap=64
    )

    assert kb.name == "法律库"
    create_mock.assert_called_once()
    # commit 一次 + 无回滚
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()
    drop_mock.assert_not_called()


async def test_create_kb_name_conflict_blocks_before_milvus(patched_milvus):
    """提前 name 查重命中 → 不调 Milvus，直接抛 NAME_CONFLICT。"""
    create_mock, drop_mock = patched_milvus
    db = _make_db_mock(name_exists=True)

    with pytest.raises(BusinessError) as exc_info:
        await kb_service.create_kb(db, name="重复")
    assert exc_info.value.code == error_codes.NAME_CONFLICT

    # Milvus 不应被调用（提前 fail-fast）
    create_mock.assert_not_called()
    drop_mock.assert_not_called()
    db.add.assert_not_called()


async def test_create_kb_milvus_failure_short_circuits(patched_milvus):
    """create_kb_collection 抛 RuntimeError → 包装成 INTERNAL_ERROR，不写 PG。"""
    create_mock, drop_mock = patched_milvus
    create_mock.side_effect = RuntimeError("milvus down")
    db = _make_db_mock(name_exists=False)

    with pytest.raises(BusinessError) as exc_info:
        await kb_service.create_kb(db, name="x")
    assert exc_info.value.code == error_codes.INTERNAL_ERROR

    # PG 写入未发生
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    # Milvus 创建失败，rollback 也不需要调（因为 collection 没建出来）
    drop_mock.assert_not_called()


async def test_create_kb_pg_failure_rolls_back_milvus(patched_milvus, monkeypatch):
    """PG commit 抛 IntegrityError → 回滚 Milvus + 抛 NAME_CONFLICT。"""
    from sqlalchemy.exc import IntegrityError

    create_mock, drop_mock = patched_milvus
    db = _make_db_mock(name_exists=False)
    db.commit.side_effect = IntegrityError("INSERT", {}, Exception("dup"))

    with pytest.raises(BusinessError) as exc_info:
        await kb_service.create_kb(db, name="x")
    assert exc_info.value.code == error_codes.NAME_CONFLICT

    db.rollback.assert_awaited_once()
    # Milvus 已建 → 必须回滚 drop
    drop_mock.assert_called_once()


async def test_create_kb_pg_generic_failure_also_rolls_back(patched_milvus):
    """PG 普通异常也要回滚 Milvus，抛 INTERNAL_ERROR。"""
    create_mock, drop_mock = patched_milvus
    db = _make_db_mock(name_exists=False)
    db.commit.side_effect = RuntimeError("db connection lost")

    with pytest.raises(BusinessError) as exc_info:
        await kb_service.create_kb(db, name="x")
    assert exc_info.value.code == error_codes.INTERNAL_ERROR
    drop_mock.assert_called_once()


async def test_create_kb_milvus_rollback_failure_logged_not_raised(
    patched_milvus, caplog
):
    """Milvus 回滚（_safe_rollback_milvus）失败不阻断原异常传播。"""
    create_mock, drop_mock = patched_milvus
    drop_mock.side_effect = RuntimeError("can't drop")
    db = _make_db_mock(name_exists=False)
    db.commit.side_effect = RuntimeError("db oops")

    with pytest.raises(BusinessError) as exc_info:
        await kb_service.create_kb(db, name="x")
    # 原始错误码保留（不被 drop 失败覆盖）
    assert exc_info.value.code == error_codes.INTERNAL_ERROR


# ──────────────── KB-04 update_kb ────────────────


async def test_update_kb_name_change_checks_conflict(monkeypatch):
    """改 name 必须先查重（排除自己）。"""
    db = _make_db_mock(name_exists=False)
    kid = uuid.uuid4()
    fake = KnowledgeBase(id=kid, name="旧", embedding_dim=4096, chunk_size=512, chunk_overlap=64)

    get_mock = AsyncMock(return_value=fake)
    monkeypatch.setattr(kb_service, "get_kb_or_raise", get_mock)
    monkeypatch.setattr(
        kb_service, "_kb_name_exists", AsyncMock(return_value=False)
    )

    result = await kb_service.update_kb(
        db, kid, name="新", description=None, description_was_set=False
    )
    assert result.name == "新"
    db.commit.assert_awaited_once()


async def test_update_kb_name_unchanged_skips_conflict_check(monkeypatch):
    """name 没改 → 不查重。"""
    db = _make_db_mock(name_exists=False)
    kid = uuid.uuid4()
    fake = KnowledgeBase(id=kid, name="同名", embedding_dim=4096, chunk_size=512, chunk_overlap=64)

    monkeypatch.setattr(kb_service, "get_kb_or_raise", AsyncMock(return_value=fake))
    name_exists_mock = AsyncMock(return_value=False)
    monkeypatch.setattr(kb_service, "_kb_name_exists", name_exists_mock)

    await kb_service.update_kb(
        db, kid, name="同名", description="新描述", description_was_set=True
    )
    # 因为 name 没变化，不应触发 _kb_name_exists
    name_exists_mock.assert_not_awaited()


async def test_update_kb_name_conflict_raises(monkeypatch):
    db = _make_db_mock()
    kid = uuid.uuid4()
    fake = KnowledgeBase(id=kid, name="旧", embedding_dim=4096, chunk_size=512, chunk_overlap=64)

    monkeypatch.setattr(kb_service, "get_kb_or_raise", AsyncMock(return_value=fake))
    monkeypatch.setattr(kb_service, "_kb_name_exists", AsyncMock(return_value=True))

    with pytest.raises(BusinessError) as exc_info:
        await kb_service.update_kb(
            db, kid, name="冲突", description=None, description_was_set=False
        )
    assert exc_info.value.code == error_codes.NAME_CONFLICT
    db.commit.assert_not_awaited()


async def test_update_kb_description_was_set_false_keeps_original(monkeypatch):
    db = _make_db_mock()
    kid = uuid.uuid4()
    fake = KnowledgeBase(
        id=kid, name="x", description="原描述", embedding_dim=4096, chunk_size=512, chunk_overlap=64
    )
    monkeypatch.setattr(kb_service, "get_kb_or_raise", AsyncMock(return_value=fake))

    await kb_service.update_kb(
        db, kid, name=None, description=None, description_was_set=False
    )
    # 没传 description → 原值保留
    assert fake.description == "原描述"


async def test_update_kb_description_was_set_true_can_clear(monkeypatch):
    db = _make_db_mock()
    kid = uuid.uuid4()
    fake = KnowledgeBase(
        id=kid, name="x", description="原描述", embedding_dim=4096, chunk_size=512, chunk_overlap=64
    )
    monkeypatch.setattr(kb_service, "get_kb_or_raise", AsyncMock(return_value=fake))

    await kb_service.update_kb(
        db, kid, name=None, description=None, description_was_set=True
    )
    # 显式传 None → 清空
    assert fake.description is None


# ──────────────── KB-05 delete_kb ────────────────


async def test_delete_kb_strict_order_milvus_first(patched_milvus, monkeypatch):
    """正常路径：先 drop Milvus 再 delete PG。"""
    create_mock, drop_mock = patched_milvus
    db = _make_db_mock()
    kid = uuid.uuid4()
    fake = KnowledgeBase(id=kid, name="x", embedding_dim=4096, chunk_size=512, chunk_overlap=64)
    monkeypatch.setattr(kb_service, "get_kb_or_raise", AsyncMock(return_value=fake))

    # 用列表记录调用顺序
    call_order: list[str] = []
    drop_mock.side_effect = lambda *a, **kw: call_order.append("milvus_drop") or True
    db.delete.side_effect = lambda *a, **kw: call_order.append("pg_delete")
    db.commit.side_effect = lambda: call_order.append("pg_commit")

    await kb_service.delete_kb(db, kid)
    # 顺序：先 Milvus，再 PG
    assert call_order == ["milvus_drop", "pg_delete", "pg_commit"]


async def test_delete_kb_milvus_failure_does_not_touch_pg(patched_milvus, monkeypatch):
    """Milvus drop 失败 → 不动 PG，整体抛 INTERNAL_ERROR。"""
    _, drop_mock = patched_milvus
    drop_mock.side_effect = RuntimeError("milvus down")

    db = _make_db_mock()
    kid = uuid.uuid4()
    fake = KnowledgeBase(id=kid, name="x", embedding_dim=4096, chunk_size=512, chunk_overlap=64)
    monkeypatch.setattr(kb_service, "get_kb_or_raise", AsyncMock(return_value=fake))

    with pytest.raises(BusinessError) as exc_info:
        await kb_service.delete_kb(db, kid)
    assert exc_info.value.code == error_codes.INTERNAL_ERROR

    db.delete.assert_not_awaited()
    db.commit.assert_not_awaited()


async def test_delete_kb_pg_failure_after_milvus_drop_raises_but_milvus_already_gone(
    patched_milvus, monkeypatch, caplog
):
    """PG 删除失败时 Milvus 已 drop，service 抛 500 并日志告警（数据不一致）。"""
    _, drop_mock = patched_milvus
    drop_mock.return_value = True

    db = _make_db_mock()
    db.commit.side_effect = RuntimeError("pg down")

    kid = uuid.uuid4()
    fake = KnowledgeBase(id=kid, name="x", embedding_dim=4096, chunk_size=512, chunk_overlap=64)
    monkeypatch.setattr(kb_service, "get_kb_or_raise", AsyncMock(return_value=fake))

    with pytest.raises(BusinessError) as exc_info:
        await kb_service.delete_kb(db, kid)
    assert exc_info.value.code == error_codes.INTERNAL_ERROR
    # Milvus 已 drop 必须在错误信息提示"请人工介入"
    assert "人工介入" in exc_info.value.message
    drop_mock.assert_called_once()
    db.rollback.assert_awaited_once()


async def test_delete_kb_not_found(monkeypatch, patched_milvus):
    _, drop_mock = patched_milvus
    db = _make_db_mock()
    kid = uuid.uuid4()
    monkeypatch.setattr(
        kb_service,
        "get_kb_or_raise",
        AsyncMock(side_effect=BusinessError(error_codes.NOT_FOUND, "x")),
    )

    with pytest.raises(BusinessError) as exc_info:
        await kb_service.delete_kb(db, kid)
    assert exc_info.value.code == error_codes.NOT_FOUND
    # 未找到 → Milvus 也不该被调用
    drop_mock.assert_not_called()


# ──────────────── count_entities_for_kb S2 stub ────────────────


async def test_count_entities_for_kb_stub_returns_zero():
    """S2 阶段 stub 返回 0，S5 接通 Neo4j 后改实查。"""
    assert await kb_service.count_entities_for_kb(uuid.uuid4()) == 0
