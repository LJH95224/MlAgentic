"""V1.5 S3.2 三联清理测试（FILE-04 / KB-05 / FILE-05）。

覆盖：
- FILE-04 _cleanup_milvus_chunks_for_file：collection 存在 / 不存在 / Milvus 未初始化 / delete 失败
- FILE-04 _cleanup_neo4j_entities_for_file：driver 未初始化 / Cypher 失败 / Cypher 参数正确
- KB-05 _revoke_kb_processing_tasks：仅 revoke processing 状态文件的任务
- KB-05 _cleanup_kb_neo4j：DETACH DELETE 整个子图
- KB-05 _cleanup_kb_upload_dir：调 kb_file_service.remove_kb_upload_root

不依赖真 Milvus / Neo4j / 真 PG。
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import kb_file_service, kb_service


# ──────────────── FILE-04 Milvus 清理 ────────────────


async def test_cleanup_milvus_when_collection_exists(monkeypatch):
    """Collection 存在 → 按 document_id 过滤 delete。"""
    mock_client = MagicMock()
    mock_client.has_collection = MagicMock(return_value=True)
    mock_client.delete = MagicMock()

    # patch 全局 get_milvus_client
    import app.rag.milvus_client as milvus_mod

    monkeypatch.setattr(milvus_mod, "get_milvus_client", lambda: mock_client)

    kb_id = uuid.uuid4()
    file_id = uuid.uuid4()
    await kb_file_service._cleanup_milvus_chunks_for_file(kb_id, file_id)

    mock_client.delete.assert_called_once()
    call_kwargs = mock_client.delete.call_args.kwargs
    # 过滤表达式应包含 document_id == "<file_id>"
    assert "document_id" in call_kwargs["filter"]
    assert str(file_id) in call_kwargs["filter"]


async def test_cleanup_milvus_when_collection_missing_is_noop(monkeypatch):
    mock_client = MagicMock()
    mock_client.has_collection = MagicMock(return_value=False)
    mock_client.delete = MagicMock()

    import app.rag.milvus_client as milvus_mod

    monkeypatch.setattr(milvus_mod, "get_milvus_client", lambda: mock_client)

    await kb_file_service._cleanup_milvus_chunks_for_file(uuid.uuid4(), uuid.uuid4())
    mock_client.delete.assert_not_called()


async def test_cleanup_milvus_when_not_initialized_does_not_raise(monkeypatch):
    """get_milvus_client 抛 RuntimeError 时仅 warning，不阻断流程。"""

    def _raise():
        raise RuntimeError("尚未初始化")

    import app.rag.milvus_client as milvus_mod

    monkeypatch.setattr(milvus_mod, "get_milvus_client", _raise)

    # 不抛异常即视为通过
    await kb_file_service._cleanup_milvus_chunks_for_file(uuid.uuid4(), uuid.uuid4())


async def test_cleanup_milvus_delete_failure_does_not_raise(monkeypatch):
    """Milvus delete 抛错只 warning，不阻断流程。"""
    mock_client = MagicMock()
    mock_client.has_collection = MagicMock(return_value=True)
    mock_client.delete = MagicMock(side_effect=RuntimeError("milvus rpc broken"))

    import app.rag.milvus_client as milvus_mod

    monkeypatch.setattr(milvus_mod, "get_milvus_client", lambda: mock_client)

    await kb_file_service._cleanup_milvus_chunks_for_file(uuid.uuid4(), uuid.uuid4())


# ──────────────── FILE-04 Neo4j 清理 ────────────────


async def test_cleanup_neo4j_uses_kb_id_and_document_id(monkeypatch):
    """Cypher 必须按 (document_id, kb_id) 二维匹配防误删。"""
    mock_session = MagicMock()
    mock_session.run = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    mock_driver = MagicMock()
    mock_driver.session = MagicMock(return_value=mock_session)

    import app.kg.neo4j_client as neo4j_mod

    monkeypatch.setattr(neo4j_mod, "get_neo4j_driver", lambda: mock_driver)

    kb_id = uuid.uuid4()
    file_id = uuid.uuid4()
    await kb_file_service._cleanup_neo4j_entities_for_file(kb_id, file_id)

    mock_session.run.assert_awaited_once()
    args, kwargs = mock_session.run.await_args
    cypher = args[0]
    # 必须用 document_id + kb_id 复合匹配
    assert "document_id" in cypher
    assert "kb_id" in cypher
    assert "DETACH DELETE" in cypher
    assert kwargs == {"document_id": str(file_id), "kb_id": str(kb_id)}


async def test_cleanup_neo4j_driver_not_initialized_does_not_raise(monkeypatch):
    def _raise():
        raise RuntimeError("尚未初始化")

    import app.kg.neo4j_client as neo4j_mod

    monkeypatch.setattr(neo4j_mod, "get_neo4j_driver", _raise)

    # 不抛异常即视为通过
    await kb_file_service._cleanup_neo4j_entities_for_file(uuid.uuid4(), uuid.uuid4())


async def test_cleanup_neo4j_cypher_failure_does_not_raise(monkeypatch):
    """Cypher 失败仅 warning，不阻断流程。"""
    mock_session = MagicMock()
    mock_session.run = AsyncMock(side_effect=RuntimeError("neo4j down"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    mock_driver = MagicMock()
    mock_driver.session = MagicMock(return_value=mock_session)

    import app.kg.neo4j_client as neo4j_mod

    monkeypatch.setattr(neo4j_mod, "get_neo4j_driver", lambda: mock_driver)

    # 不抛异常即视为通过
    await kb_file_service._cleanup_neo4j_entities_for_file(uuid.uuid4(), uuid.uuid4())


# ──────────────── KB-05 revoke 任务 ────────────────


async def test_revoke_kb_processing_tasks_revokes_each(monkeypatch):
    """processing 状态的文件应被 revoke；其它状态不应。"""
    kb_id = uuid.uuid4()
    f1 = (uuid.uuid4(), "task-1")
    f2 = (uuid.uuid4(), "task-2")
    f3 = (uuid.uuid4(), None)  # 没 task_id 应跳过

    # mock db.execute 返带 .all() 的结果
    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.all = MagicMock(return_value=[f1, f2, f3])
    mock_db.execute = AsyncMock(return_value=mock_result)

    # mock celery_app.control.revoke
    # 注意：app/tasks/__init__.py 用了 `from .celery_app import celery_app`，
    # 这让 `app.tasks.celery_app` 名字在 app.tasks 命名空间下被 Celery 实例遮蔽；
    # `import app.tasks.celery_app as x` 拿到的不是模块对象。必须从 sys.modules 取
    import sys

    import app.tasks.celery_app  # 触发 import

    celery_mod = sys.modules["app.tasks.celery_app"]

    mock_control = MagicMock()
    mock_control.revoke = MagicMock()
    monkeypatch.setattr(celery_mod.celery_app, "control", mock_control)

    await kb_service._revoke_kb_processing_tasks(mock_db, kb_id)

    # task-1 + task-2 都被 revoke；task None 不调
    assert mock_control.revoke.call_count == 2
    revoked_ids = [c.args[0] for c in mock_control.revoke.call_args_list]
    assert "task-1" in revoked_ids
    assert "task-2" in revoked_ids


async def test_revoke_kb_processing_tasks_no_processing_files(monkeypatch):
    """没有 processing 文件 → 不调 celery.revoke。"""
    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.all = MagicMock(return_value=[])
    mock_db.execute = AsyncMock(return_value=mock_result)

    import sys

    import app.tasks.celery_app  # 触发 import

    celery_mod = sys.modules["app.tasks.celery_app"]

    mock_control = MagicMock()
    mock_control.revoke = MagicMock()
    monkeypatch.setattr(celery_mod.celery_app, "control", mock_control)

    await kb_service._revoke_kb_processing_tasks(mock_db, uuid.uuid4())
    mock_control.revoke.assert_not_called()


async def test_revoke_kb_processing_tasks_revoke_failure_does_not_raise(monkeypatch):
    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.all = MagicMock(return_value=[(uuid.uuid4(), "task-x")])
    mock_db.execute = AsyncMock(return_value=mock_result)

    import sys

    import app.tasks.celery_app  # 触发 import

    celery_mod = sys.modules["app.tasks.celery_app"]

    mock_control = MagicMock()
    mock_control.revoke = MagicMock(side_effect=RuntimeError("broker down"))
    monkeypatch.setattr(celery_mod.celery_app, "control", mock_control)

    # 不抛异常即通过
    await kb_service._revoke_kb_processing_tasks(mock_db, uuid.uuid4())


# ──────────────── KB-05 Neo4j 子图清理 ────────────────


async def test_cleanup_kb_neo4j_uses_kb_id_filter(monkeypatch):
    mock_session = MagicMock()
    mock_session.run = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    mock_driver = MagicMock()
    mock_driver.session = MagicMock(return_value=mock_session)

    import app.kg.neo4j_client as neo4j_mod

    monkeypatch.setattr(neo4j_mod, "get_neo4j_driver", lambda: mock_driver)

    kb_id = uuid.uuid4()
    await kb_service._cleanup_kb_neo4j(kb_id)

    mock_session.run.assert_awaited_once()
    args, kwargs = mock_session.run.await_args
    cypher = args[0]
    # 必须按 kb_id 过滤
    assert "kb_id" in cypher
    assert "DETACH DELETE" in cypher
    assert kwargs == {"kb_id": str(kb_id)}


async def test_cleanup_kb_neo4j_driver_not_initialized_does_not_raise(monkeypatch):
    def _raise():
        raise RuntimeError("尚未初始化")

    import app.kg.neo4j_client as neo4j_mod

    monkeypatch.setattr(neo4j_mod, "get_neo4j_driver", _raise)

    await kb_service._cleanup_kb_neo4j(uuid.uuid4())


# ──────────────── KB-05 磁盘清理 ────────────────


def test_cleanup_kb_upload_dir_calls_remove_helper(monkeypatch):
    called_with = []

    def _fake_remove(kb_id):
        called_with.append(kb_id)

    import app.services.kb_file_service as fsvc

    monkeypatch.setattr(fsvc, "remove_kb_upload_root", _fake_remove)

    kb_id = uuid.uuid4()
    kb_service._cleanup_kb_upload_dir(kb_id)
    assert called_with == [kb_id]


def test_cleanup_kb_upload_dir_failure_does_not_raise(monkeypatch):
    def _raise(kb_id):
        raise OSError("permission denied")

    import app.services.kb_file_service as fsvc

    monkeypatch.setattr(fsvc, "remove_kb_upload_root", _raise)

    # 不抛异常即通过
    kb_service._cleanup_kb_upload_dir(uuid.uuid4())


# ──────────────── KB-05 端到端协调 ────────────────


async def test_delete_kb_orchestrates_5_steps_in_order(monkeypatch):
    """KB-05 应按"revoke → milvus drop → neo4j → PG → 磁盘"顺序调用各步骤。"""
    call_order: list[str] = []

    # 1) get_kb_or_raise 返一个假 KB
    fake_kb = MagicMock()
    fake_kb.id = uuid.uuid4()
    monkeypatch.setattr(
        kb_service, "get_kb_or_raise", AsyncMock(return_value=fake_kb)
    )

    # 2) 各步骤打点
    async def _track_revoke(db, kb_id):
        call_order.append("revoke")

    def _track_milvus_drop(kb_id):
        call_order.append("milvus_drop")
        return True

    async def _track_neo4j(kb_id):
        call_order.append("neo4j")

    def _track_disk(kb_id):
        call_order.append("disk")

    monkeypatch.setattr(kb_service, "_revoke_kb_processing_tasks", _track_revoke)
    monkeypatch.setattr(kb_service, "drop_kb_collection", _track_milvus_drop)
    monkeypatch.setattr(kb_service, "_cleanup_kb_neo4j", _track_neo4j)
    monkeypatch.setattr(kb_service, "_cleanup_kb_upload_dir", _track_disk)

    # mock db
    mock_db = MagicMock()
    mock_db.delete = AsyncMock(
        side_effect=lambda x: call_order.append("pg_delete")
    )
    mock_db.commit = AsyncMock(
        side_effect=lambda: call_order.append("pg_commit")
    )
    mock_db.rollback = AsyncMock()

    await kb_service.delete_kb(mock_db, fake_kb.id)

    # 验顺序
    assert call_order == [
        "revoke",
        "milvus_drop",
        "neo4j",
        "pg_delete",
        "pg_commit",
        "disk",
    ]


async def test_delete_kb_milvus_failure_short_circuits_no_neo4j_no_pg(monkeypatch):
    """Milvus drop 失败 → 不动 Neo4j / PG / 磁盘。"""
    from app.api import error_codes
    from app.api.exceptions import BusinessError

    fake_kb = MagicMock()
    fake_kb.id = uuid.uuid4()
    monkeypatch.setattr(
        kb_service, "get_kb_or_raise", AsyncMock(return_value=fake_kb)
    )

    async def _track_revoke(db, kb_id):
        pass

    monkeypatch.setattr(kb_service, "_revoke_kb_processing_tasks", _track_revoke)
    monkeypatch.setattr(
        kb_service,
        "drop_kb_collection",
        MagicMock(side_effect=RuntimeError("milvus down")),
    )

    neo4j_called = []
    pg_delete_called = []
    disk_called = []

    async def _neo4j_should_not_be_called(kb_id):
        neo4j_called.append(True)

    def _disk_should_not_be_called(kb_id):
        disk_called.append(True)

    monkeypatch.setattr(
        kb_service, "_cleanup_kb_neo4j", _neo4j_should_not_be_called
    )
    monkeypatch.setattr(
        kb_service, "_cleanup_kb_upload_dir", _disk_should_not_be_called
    )

    mock_db = MagicMock()
    mock_db.delete = AsyncMock(side_effect=lambda x: pg_delete_called.append(True))
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    with pytest.raises(BusinessError) as exc_info:
        await kb_service.delete_kb(mock_db, fake_kb.id)
    assert exc_info.value.code == error_codes.INTERNAL_ERROR

    assert not neo4j_called
    assert not pg_delete_called
    assert not disk_called
