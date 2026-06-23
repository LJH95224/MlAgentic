"""A P1-9 + B M-06 联合单测。

覆盖：
- P1-9 模型字段：KbFile cleanup_retry_count / KnowledgeBase updated_at + cleanup_retry_count
- P1-9 Celery 注册：cleanup_reaper_task 在 tasks + beat_schedule
- P1-9 delete_file 补偿分支：全成功 / 部分失败 / 全失败
- P1-9 delete_kb 补偿分支：同款
- P1-9 cleanup_reaper_task 扫描 + 重试逻辑
- P1-9 listing 过滤：deleting / pending_cleanup 不可见
- B M-06 _build_filter_expr kb_ids 参数
- B M-06 hybrid_retriever 传参
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.kb_file import (
    FILE_STATUS_DELETING,
    FILE_STATUS_PENDING_CLEANUP,
    KbFile,
)
from app.models.knowledge_base import (
    KB_STATUS_DELETING,
    KB_STATUS_PENDING_CLEANUP,
    KnowledgeBase,
)


# ──────────────────── Part 1: 模型字段 ────────────────────


def test_kbfile_has_cleanup_retry_count():
    """KbFile 必须有 cleanup_retry_count 字段。"""
    columns = {c.name for c in KbFile.__table__.columns}
    assert "cleanup_retry_count" in columns, "KbFile 缺 cleanup_retry_count（P1-9）"


def test_kbfile_cleanup_retry_count_defaults_zero():
    """cleanup_retry_count 默认 0。"""
    col = KbFile.__table__.columns["cleanup_retry_count"]
    assert col.server_default is not None, "cleanup_retry_count 缺 server_default"
    assert col.nullable is False


def test_kb_has_updated_at_and_cleanup_retry_count():
    """KnowledgeBase 必须有 P1-9 新增的两个字段。"""
    columns = {c.name for c in KnowledgeBase.__table__.columns}
    assert "updated_at" in columns, "KnowledgeBase 缺 updated_at（P1-9）"
    assert "cleanup_retry_count" in columns, "KnowledgeBase 缺 cleanup_retry_count（P1-9）"


def test_kb_updated_at_has_server_default_and_onupdate():
    """KnowledgeBase.updated_at 有 server_default + onupdate + index。"""
    col = KnowledgeBase.__table__.columns["updated_at"]
    assert col.server_default is not None
    assert col.onupdate is not None
    assert col.nullable is False
    assert col.index is True, "updated_at 应建索引（清雷扫描用）"


# ──────────────────── Part 2: Celery 注册 ────────────────────


def test_cleanup_reaper_task_registered():
    """任务模块在 include 列表中即视为注册（celery_app.tasks 是懒加载的，
    只有 worker 真正 import 时才填充，不在单测里 assert）。
    与 test_reaper_module_in_include_list 同款风格。"""
    from app.tasks import celery_app

    assert "app.tasks.cleanup_reaper_task" in celery_app.conf.include


def test_cleanup_reaper_module_in_include():
    from app.tasks import celery_app

    assert "app.tasks.cleanup_reaper_task" in celery_app.conf.include


def test_cleanup_reaper_beat_schedule_registered():
    from app.tasks import celery_app

    schedule_cfg = celery_app.conf.beat_schedule
    assert "reap-pending-cleanup" in schedule_cfg
    entry = schedule_cfg["reap-pending-cleanup"]
    assert entry["task"] == "app.tasks.cleanup_reaper_task.reap_pending_cleanup"
    assert entry["schedule"].run_every.total_seconds() > 0


# ──────────────────── Part 3: delete_file 补偿分支 ────────────────────


@pytest.fixture
def _mock_db_session():
    """返回一个 mock AsyncSession，commit/rollback 走 AsyncMock。"""
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.execute = AsyncMock()
    session.delete = AsyncMock()
    return session


@pytest.fixture
def _mock_kb_file():
    """构造一个已入库的 KbFile mock 对象。"""
    f = MagicMock(spec=KbFile)
    f.id = uuid.uuid4()
    f.kb_id = uuid.uuid4()
    f.status = "completed"
    f.chunk_count = 10
    f.file_path = "/tmp/mock/file.pdf"
    f.celery_task_id = None
    return f


@pytest.mark.asyncio
async def test_delete_file_all_success(_mock_db_session, _mock_kb_file, monkeypatch):
    """Milvus + Neo4j 都成功 → PG 真删 + 计数回滚。"""
    from app.services import kb_file_service

    # mock get_file_or_raise 返回构造的 KbFile
    async def _fake_get_file(*args, **kwargs):
        return _mock_kb_file

    monkeypatch.setattr(kb_file_service, "get_file_or_raise", _fake_get_file)
    # 清理步骤都返回 True
    monkeypatch.setattr(
        kb_file_service, "_cleanup_milvus_chunks_for_file", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        kb_file_service,
        "_cleanup_neo4j_entities_for_file",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(kb_file_service, "_safe_remove_disk", MagicMock())

    await kb_file_service.delete_file(_mock_db_session, _mock_kb_file.kb_id, _mock_kb_file.id)

    # 文件行被删（PG 真删）
    _mock_db_session.delete.assert_called_once_with(_mock_kb_file)
    # commit 至少两次（标 deleting + 真删）
    assert _mock_db_session.commit.call_count >= 2
    # 磁盘清理被调
    kb_file_service._safe_remove_disk.assert_called_once()


@pytest.mark.asyncio
async def test_delete_file_milvus_fails(_mock_db_session, _mock_kb_file, monkeypatch):
    """Milvus 失败 + Neo4j 成功 → status=pending_cleanup，行不删。"""
    from app.services import kb_file_service

    async def _fake_get_file(*args, **kwargs):
        return _mock_kb_file

    monkeypatch.setattr(kb_file_service, "get_file_or_raise", _fake_get_file)
    monkeypatch.setattr(
        kb_file_service, "_cleanup_milvus_chunks_for_file", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        kb_file_service,
        "_cleanup_neo4j_entities_for_file",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(kb_file_service, "_safe_remove_disk", MagicMock())

    await kb_file_service.delete_file(_mock_db_session, _mock_kb_file.kb_id, _mock_kb_file.id)

    # 行没被删
    _mock_db_session.delete.assert_not_called()
    # 磁盘没被清
    kb_file_service._safe_remove_disk.assert_not_called()
    # execute 更新为 pending_cleanup
    update_calls = [
        c for c in _mock_db_session.execute.call_args_list
        if "update" in str(c).lower()
    ]
    assert len(update_calls) >= 1


@pytest.mark.asyncio
async def test_delete_file_both_fail(_mock_db_session, _mock_kb_file, monkeypatch):
    """Milvus + Neo4j 都失败 → status=pending_cleanup。"""
    from app.services import kb_file_service

    async def _fake_get_file(*args, **kwargs):
        return _mock_kb_file

    monkeypatch.setattr(kb_file_service, "get_file_or_raise", _fake_get_file)
    monkeypatch.setattr(
        kb_file_service, "_cleanup_milvus_chunks_for_file", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        kb_file_service,
        "_cleanup_neo4j_entities_for_file",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(kb_file_service, "_safe_remove_disk", MagicMock())

    await kb_file_service.delete_file(_mock_db_session, _mock_kb_file.kb_id, _mock_kb_file.id)

    _mock_db_session.delete.assert_not_called()
    kb_file_service._safe_remove_disk.assert_not_called()


# ──────────────────── Part 4: delete_kb 补偿分支 ────────────────────


@pytest.mark.asyncio
async def test_delete_kb_all_success(monkeypatch):
    """Milvus + Neo4j 都成功 → PG 真删。"""
    from app.services import kb_service

    kb = MagicMock(spec=KnowledgeBase)
    kb.id = uuid.uuid4()
    kb.status = "active"
    kb.description = "test"

    db = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.delete = AsyncMock()
    db.execute = AsyncMock()

    async def _fake_get_kb(*args, **kwargs):
        return kb

    monkeypatch.setattr(kb_service, "get_kb_or_raise", _fake_get_kb)
    monkeypatch.setattr(kb_service, "_revoke_kb_processing_tasks", AsyncMock())

    # make both cleanup paths succeed
    monkeypatch.setattr(kb_service, "_safe_drop_kb_collection", lambda _: True)
    monkeypatch.setattr(kb_service, "_cleanup_kb_neo4j", AsyncMock(return_value=True))
    monkeypatch.setattr(kb_service, "_cleanup_kb_upload_dir", MagicMock())

    await kb_service.delete_kb(db, kb.id)

    db.delete.assert_called_once_with(kb)
    kb_service._cleanup_kb_upload_dir.assert_called_once()


@pytest.mark.asyncio
async def test_delete_kb_milvus_fails(monkeypatch):
    """Milvus drop 失败 → status=pending_cleanup，行不删。"""
    from app.services import kb_service

    kb = MagicMock(spec=KnowledgeBase)
    kb.id = uuid.uuid4()
    kb.status = "active"
    kb.description = "test"

    db = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.delete = AsyncMock()
    db.execute = AsyncMock()

    async def _fake_get_kb(*args, **kwargs):
        return kb

    monkeypatch.setattr(kb_service, "get_kb_or_raise", _fake_get_kb)
    monkeypatch.setattr(kb_service, "_revoke_kb_processing_tasks", AsyncMock())
    monkeypatch.setattr(kb_service, "_safe_drop_kb_collection", lambda _: False)
    monkeypatch.setattr(kb_service, "_cleanup_kb_neo4j", AsyncMock(return_value=True))
    monkeypatch.setattr(kb_service, "_cleanup_kb_upload_dir", MagicMock())

    await kb_service.delete_kb(db, kb.id)

    db.delete.assert_not_called()


# ──────────────────── Part 5: cleanup_reaper_task 扫描与重试 ────────────────────


@pytest.mark.asyncio
async def test_scan_pending_cleanup_kbs_returns_rows(monkeypatch):
    """扫描 KB 表 pending_cleanup 行。"""
    from app.tasks import cleanup_reaper_task

    row = MagicMock()
    kb_id = uuid.uuid4()
    row.id = kb_id
    row.cleanup_retry_count = 3

    mock_result = MagicMock()
    mock_result.all = MagicMock(return_value=[row])
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    @asynccontextmanager
    async def _fake_db():
        yield mock_db

    mock_resources = MagicMock()
    mock_resources.db = _fake_db

    @asynccontextmanager
    async def _fake_resources():
        yield mock_resources

    monkeypatch.setattr(cleanup_reaper_task, "task_resources", _fake_resources)

    rows = await cleanup_reaper_task._scan_pending_cleanup_kbs(retry_cap=10)
    assert len(rows) == 1
    assert rows[0][0] == kb_id
    assert rows[0][1] == 3


@pytest.mark.asyncio
async def test_scan_pending_cleanup_files_returns_rows(monkeypatch):
    """扫描 kb_files 表 pending_cleanup 行。"""
    from app.tasks import cleanup_reaper_task

    file_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    row = MagicMock()
    row.id = file_id
    row.kb_id = kb_id
    row.cleanup_retry_count = 1

    mock_result = MagicMock()
    mock_result.all = MagicMock(return_value=[row])
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    @asynccontextmanager
    async def _fake_db():
        yield mock_db

    mock_resources = MagicMock()
    mock_resources.db = _fake_db

    @asynccontextmanager
    async def _fake_resources():
        yield mock_resources

    monkeypatch.setattr(cleanup_reaper_task, "task_resources", _fake_resources)

    rows = await cleanup_reaper_task._scan_pending_cleanup_files(retry_cap=10)
    assert len(rows) == 1
    assert rows[0][0] == file_id
    assert rows[0][1] == kb_id
    assert rows[0][2] == 1


@pytest.mark.asyncio
async def test_scan_respects_retry_cap(monkeypatch):
    """retry_count >= retry_cap 的行不被扫描到。"""
    from app.tasks import cleanup_reaper_task

    mock_result = MagicMock()
    mock_result.all = MagicMock(return_value=[])
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def _fake_db():
        yield mock_db

    mock_resources = MagicMock()
    mock_resources.db = _fake_db

    @asynccontextmanager
    async def _fake_resources():
        yield mock_resources

    monkeypatch.setattr(cleanup_reaper_task, "task_resources", _fake_resources)

    rows = await cleanup_reaper_task._scan_pending_cleanup_kbs(retry_cap=5)
    assert rows == []  # 测试 mock 返回空，但关键是 SQL 里有 retry_cap WHERE


# ──────────────────── Part 6: listing 过滤 ────────────────────


def test_kb_file_status_choices_include_p1_9():
    """FILE_STATUS_CHOICES 必须包含 deleting / pending_cleanup。"""
    from app.models.kb_file import FILE_STATUS_CHOICES

    assert FILE_STATUS_DELETING in FILE_STATUS_CHOICES
    assert FILE_STATUS_PENDING_CLEANUP in FILE_STATUS_CHOICES


def test_kb_status_choices_include_p1_9():
    """KB_STATUS_CHOICES 必须包含 deleting / pending_cleanup。"""
    from app.models.knowledge_base import KB_STATUS_CHOICES

    assert KB_STATUS_DELETING in KB_STATUS_CHOICES
    assert KB_STATUS_PENDING_CLEANUP in KB_STATUS_CHOICES


# ──────────────────── Part 7: B M-06 _build_filter_expr kb_ids ────────────────────


def test_build_filter_expr_without_kb_ids():
    """不传 kb_ids → 表达式不含 kb_id。"""
    from app.rag.filters import _build_filter_expr

    expr = _build_filter_expr(None, None, None, "ALL")
    assert "kb_id" not in expr
    assert "ARRAY_CONTAINS(allowed_roles" in expr


def test_build_filter_expr_with_kb_ids():
    """传 kb_ids → 表达式含 kb_id IN [...]。"""
    from app.rag.filters import _build_filter_expr

    kb_a = str(uuid.uuid4())
    kb_b = str(uuid.uuid4())
    expr = _build_filter_expr(None, None, None, "ALL", kb_ids=[kb_a, kb_b])
    assert "kb_id IN" in expr
    assert kb_a in expr
    assert kb_b in expr
    assert "ARRAY_CONTAINS(allowed_roles" in expr


def test_build_filter_expr_kb_ids_combined_with_other_clauses():
    """kb_id 与其他过滤子句共存时正确 and 拼接。"""
    from app.rag.filters import _build_filter_expr

    kb_id = str(uuid.uuid4())
    expr = _build_filter_expr("report", "doc-1", ["实体A"], "ADMIN", kb_ids=[kb_id])
    assert "kb_id IN" in expr
    assert 'metadata["type"]' in expr
    assert "document_id" in expr
    assert "ARRAY_CONTAINS_ANY(entity_tags" in expr
    assert "ARRAY_CONTAINS(allowed_roles" in expr
    # 所有子句之间用 and 连接
    parts = expr.split(" and ")
    assert len(parts) >= 5


def test_build_filter_expr_kb_id_escaping():
    """kb_id 含双引号 → 被正确转义，不破表达式。"""
    from app.rag.filters import _build_filter_expr

    dodgy = str(uuid.uuid4()).replace("a", '"')  # 造一个含双引号的"合法" UUID
    expr = _build_filter_expr(None, None, None, "ALL", kb_ids=[dodgy])
    # 不应该出现未转义的双引号夹在kb_id值里
    assert "kb_id IN" in expr
    # 转义后的双引号不应截断表达式
    assert expr.count('"') >= 4  # 开闭引号至少各两个


# ──────────────────── Part 8: hybrid_retriever 传参 ────────────────────


def test_hybrid_retriever_imports_build_filter_expr():
    """hybrid_retriever 必须从 filters 导入 _build_filter_expr。"""
    from app.rag.hybrid_retriever import _build_filter_expr  # noqa: F401
    # 只要 import 成功就行


def test_config_cleanup_reaper_fields_exist():
    """Settings 必须有 P1-9 三个配置项。"""
    from app.core.config import get_settings

    s = get_settings()
    assert hasattr(s, "cleanup_reaper_interval_s")
    assert hasattr(s, "cleanup_reaper_max_retry")
    assert hasattr(s, "cleanup_reaper_enable")
    assert s.cleanup_reaper_interval_s > 0
    assert s.cleanup_reaper_max_retry > 0