"""V1.5 FILE-01~05 kb_file_service 单测（mock DB + mock Celery，CI 友好）。

覆盖 endpoint mock 测不到的"service 内部协调"逻辑：
- _save_upload_streaming：边读边量 + 超限抛 + 删半成品 + IO 失败清理
- upload_file：扩展名校验 + MIME 二次校验（不抛只 warning）+ Celery 触发后 task_id 写回
- delete_file：状态分支（processing 才 revoke）+ 计数减回
- reindex_file：磁盘文件不存在 → 404 + 状态重置

真实数据库行为走集成测试。
"""

import io
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api import error_codes
from app.api.exceptions import BusinessError
from app.models.kb_file import (
    FILE_STATUS_PENDING,
    FILE_STATUS_PROCESSING,
    KbFile,
)
from app.models.knowledge_base import KnowledgeBase
from app.services import kb_file_service


# ──────────────── _save_upload_streaming ────────────────


def test_save_upload_streaming_writes_full_content(tmp_path):
    src = io.BytesIO(b"hello world" * 100)
    dst = tmp_path / "out.bin"
    size = kb_file_service._save_upload_streaming(
        src, dst, size_limit_bytes=10_000
    )
    assert dst.exists()
    assert size == 11 * 100
    assert dst.read_bytes() == b"hello world" * 100


def test_save_upload_streaming_creates_parent_dirs(tmp_path):
    src = io.BytesIO(b"x")
    dst = tmp_path / "deep" / "nested" / "out.bin"
    kb_file_service._save_upload_streaming(src, dst, size_limit_bytes=1024)
    assert dst.exists()


def test_save_upload_streaming_rejects_over_size(tmp_path):
    src = io.BytesIO(b"x" * 5000)
    dst = tmp_path / "out.bin"
    with pytest.raises(BusinessError) as exc_info:
        kb_file_service._save_upload_streaming(src, dst, size_limit_bytes=1024)
    assert exc_info.value.code == error_codes.FILE_TOO_LARGE
    # 半成品已被清理
    assert not dst.exists()


def test_save_upload_streaming_io_failure_cleans_half_file(tmp_path):
    """模拟写盘中途 IO 错误 → 半成品被清掉、抛 INTERNAL_ERROR。"""
    src = MagicMock()
    src.read = MagicMock(side_effect=OSError("disk full"))
    dst = tmp_path / "out.bin"

    with pytest.raises(BusinessError) as exc_info:
        kb_file_service._save_upload_streaming(
            src, dst, size_limit_bytes=10000
        )
    assert exc_info.value.code == error_codes.INTERNAL_ERROR
    assert not dst.exists()


def test_save_upload_streaming_exact_limit_allowed(tmp_path):
    """size == limit 应允许（边界值）。"""
    src = io.BytesIO(b"x" * 1024)
    dst = tmp_path / "out.bin"
    size = kb_file_service._save_upload_streaming(src, dst, size_limit_bytes=1024)
    assert size == 1024


# ──────────────── _build_storage_path ────────────────


def test_build_storage_path_structure(monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", "/var/uploads")
    from app.core.config import get_settings

    get_settings.cache_clear()

    kb_id = uuid.uuid4()
    file_id = uuid.uuid4()
    path = kb_file_service._build_storage_path(kb_id, file_id, "sample.pdf")
    # 三层结构：{UPLOAD_DIR}/{kb_id}/{file_id}/{filename}
    assert path.name == "sample.pdf"
    assert str(file_id) in str(path)
    assert str(kb_id) in str(path)


# ──────────────── upload_file ────────────────


def _make_db():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    return db


def _patch_kb_exists(monkeypatch, kb=None):
    """让 get_kb_or_raise 返回一个假 KB（默认 4096 dim）。"""
    if kb is None:
        kb = KnowledgeBase(
            id=uuid.uuid4(),
            name="测试库",
            embedding_dim=4096,
            chunk_size=512,
            chunk_overlap=64,
        )
    monkeypatch.setattr(
        kb_file_service, "get_kb_or_raise", AsyncMock(return_value=kb)
    )
    return kb


def _patch_celery_delay(monkeypatch, task_id="task-mock-id"):
    """让 ingest_task.parse_and_ingest_task.delay 返回 task_id；返回 mock 对象方便断言。"""
    mock_task = MagicMock()
    mock_task.delay = MagicMock(return_value=MagicMock(id=task_id))

    import app.tasks.ingest_task as ingest_mod

    monkeypatch.setattr(ingest_mod, "parse_and_ingest_task", mock_task)
    return mock_task


async def test_upload_file_happy_path(monkeypatch, tmp_path):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    from app.core.config import get_settings

    get_settings.cache_clear()

    _patch_kb_exists(monkeypatch)
    mock_task = _patch_celery_delay(monkeypatch, task_id="t-1")

    db = _make_db()
    src = io.BytesIO(b"hello world content")

    kb_file = await kb_file_service.upload_file(
        db,
        uuid.uuid4(),
        src_stream=src,
        filename="sample.txt",
        declared_mime="text/plain",
    )

    assert kb_file.filename == "sample.txt"
    assert kb_file.status == FILE_STATUS_PENDING
    assert kb_file.file_size == len(b"hello world content")
    # PG 写了 file 行
    db.add.assert_called_once()
    # Celery 任务被触发
    mock_task.delay.assert_called_once()
    # 磁盘文件真的写进 tmp_path 下
    assert Path(kb_file.file_path).exists()


async def test_upload_file_unsupported_extension(monkeypatch, tmp_path):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    from app.core.config import get_settings

    get_settings.cache_clear()

    _patch_kb_exists(monkeypatch)
    db = _make_db()

    with pytest.raises(BusinessError) as exc_info:
        await kb_file_service.upload_file(
            db,
            uuid.uuid4(),
            src_stream=io.BytesIO(b"x"),
            filename="bad.xls",
            declared_mime="application/vnd.ms-excel",
        )
    assert exc_info.value.code == error_codes.UNSUPPORTED_MEDIA
    # 没写盘、没写 DB、没触发任务
    db.add.assert_not_called()


async def test_upload_file_mime_mismatch_only_warns(monkeypatch, tmp_path, caplog):
    """Windows 上 .md 常被识别为 octet-stream；不应抛错。"""
    import logging

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    from app.core.config import get_settings

    get_settings.cache_clear()

    _patch_kb_exists(monkeypatch)
    _patch_celery_delay(monkeypatch)

    db = _make_db()
    src = io.BytesIO(b"# markdown content")

    with caplog.at_level(logging.WARNING, logger="app.services.kb_file_service"):
        kb_file = await kb_file_service.upload_file(
            db,
            uuid.uuid4(),
            src_stream=src,
            filename="notes.md",
            declared_mime="application/octet-stream",
        )
    assert kb_file.filename == "notes.md"
    # MIME 不匹配 warning 应该发出来
    assert any("MIME 与扩展名不匹配" in r.message for r in caplog.records)


async def test_upload_file_kb_not_found(monkeypatch):
    """KB 不存在 → 透传 BusinessError(NOT_FOUND)，service 不调下游。"""
    monkeypatch.setattr(
        kb_file_service,
        "get_kb_or_raise",
        AsyncMock(
            side_effect=BusinessError(error_codes.NOT_FOUND, "知识库 x 不存在")
        ),
    )
    db = _make_db()

    with pytest.raises(BusinessError) as exc_info:
        await kb_file_service.upload_file(
            db,
            uuid.uuid4(),
            src_stream=io.BytesIO(b"x"),
            filename="a.txt",
            declared_mime="text/plain",
        )
    assert exc_info.value.code == error_codes.NOT_FOUND
    db.add.assert_not_called()


async def test_upload_file_too_large(monkeypatch, tmp_path):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "1")  # 1MB
    from app.core.config import get_settings

    get_settings.cache_clear()

    _patch_kb_exists(monkeypatch)
    db = _make_db()
    src = io.BytesIO(b"x" * (2 * 1024 * 1024))  # 2MB

    with pytest.raises(BusinessError) as exc_info:
        await kb_file_service.upload_file(
            db,
            uuid.uuid4(),
            src_stream=src,
            filename="big.pdf",
            declared_mime="application/pdf",
        )
    assert exc_info.value.code == error_codes.FILE_TOO_LARGE
    db.add.assert_not_called()


async def test_upload_file_celery_failure_does_not_block_upload(
    monkeypatch, tmp_path, caplog
):
    """Celery 不可达时上传仍成功（task_id=None，等用户 reindex 补救）。"""
    import logging

    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    from app.core.config import get_settings

    get_settings.cache_clear()

    _patch_kb_exists(monkeypatch)

    # 让 parse_and_ingest_task.delay 抛错
    mock_task = MagicMock()
    mock_task.delay = MagicMock(side_effect=RuntimeError("broker down"))
    import app.tasks.ingest_task as ingest_mod

    monkeypatch.setattr(ingest_mod, "parse_and_ingest_task", mock_task)

    db = _make_db()

    with caplog.at_level(logging.ERROR, logger="app.services.kb_file_service"):
        kb_file = await kb_file_service.upload_file(
            db,
            uuid.uuid4(),
            src_stream=io.BytesIO(b"x"),
            filename="a.txt",
            declared_mime="text/plain",
        )
    # 上传成功，但 celery_task_id 为 None
    assert kb_file.filename == "a.txt"
    assert kb_file.celery_task_id is None
    # ERROR 日志记录了 Celery 失败
    assert any("Celery" in r.message for r in caplog.records)


# ──────────────── delete_file ────────────────


async def test_delete_file_processing_revokes_task(monkeypatch):
    """status=processing → 必须 revoke Celery 任务。"""
    file_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    fake = KbFile(
        id=file_id,
        kb_id=kb_id,
        filename="x.pdf",
        file_path="/tmp/nope/x.pdf",
        file_size=100,
        mime_type="application/pdf",
        status=FILE_STATUS_PROCESSING,
        progress=35,
        chunk_count=0,
        celery_task_id="task-abc",
    )
    monkeypatch.setattr(
        kb_file_service, "get_file_or_raise", AsyncMock(return_value=fake)
    )

    revoke_mock = MagicMock()
    monkeypatch.setattr(kb_file_service, "_safe_revoke_task", revoke_mock)

    db = _make_db()
    db.delete = AsyncMock()

    await kb_file_service.delete_file(db, kb_id, file_id)
    revoke_mock.assert_called_once_with("task-abc")


async def test_delete_file_completed_does_not_revoke(monkeypatch):
    """status=completed → 不应该 revoke（任务早已结束）。"""
    from app.models.kb_file import FILE_STATUS_COMPLETED

    file_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    fake = KbFile(
        id=file_id,
        kb_id=kb_id,
        filename="x.pdf",
        file_path="/tmp/nope/x.pdf",
        file_size=100,
        mime_type="application/pdf",
        status=FILE_STATUS_COMPLETED,
        progress=100,
        chunk_count=20,
    )
    monkeypatch.setattr(
        kb_file_service, "get_file_or_raise", AsyncMock(return_value=fake)
    )
    revoke_mock = MagicMock()
    monkeypatch.setattr(kb_file_service, "_safe_revoke_task", revoke_mock)

    db = _make_db()
    db.delete = AsyncMock()

    await kb_file_service.delete_file(db, kb_id, file_id)
    revoke_mock.assert_not_called()


async def test_delete_file_disk_cleanup_called(monkeypatch):
    """删除流程末尾应清磁盘文件。"""
    from app.models.kb_file import FILE_STATUS_COMPLETED

    file_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    fake = KbFile(
        id=file_id,
        kb_id=kb_id,
        filename="x.pdf",
        file_path="/tmp/uploads/kb/file/x.pdf",
        file_size=100,
        mime_type="application/pdf",
        status=FILE_STATUS_COMPLETED,
        progress=100,
        chunk_count=5,
    )
    monkeypatch.setattr(
        kb_file_service, "get_file_or_raise", AsyncMock(return_value=fake)
    )
    disk_mock = MagicMock()
    monkeypatch.setattr(kb_file_service, "_safe_remove_disk", disk_mock)

    db = _make_db()
    db.delete = AsyncMock()

    await kb_file_service.delete_file(db, kb_id, file_id)
    disk_mock.assert_called_once_with("/tmp/uploads/kb/file/x.pdf")


# ──────────────── reindex_file ────────────────


async def test_reindex_file_disk_missing_raises_not_found(monkeypatch):
    """磁盘文件丢失 → 404 提示重新上传。"""
    fake = KbFile(
        id=uuid.uuid4(),
        kb_id=uuid.uuid4(),
        filename="x.pdf",
        file_path="/tmp/__definitely_does_not_exist__/x.pdf",
        file_size=100,
        mime_type="application/pdf",
        status=FILE_STATUS_PENDING,
        progress=0,
    )
    monkeypatch.setattr(
        kb_file_service, "get_file_or_raise", AsyncMock(return_value=fake)
    )

    db = _make_db()
    with pytest.raises(BusinessError) as exc_info:
        await kb_file_service.reindex_file(db, fake.kb_id, fake.id)
    assert exc_info.value.code == error_codes.NOT_FOUND
    assert "重新上传" in exc_info.value.message


async def test_reindex_file_resets_state(monkeypatch, tmp_path):
    """磁盘文件在 → 状态重置 + 触发新任务。"""
    from app.models.kb_file import FILE_STATUS_FAILED

    # 准备磁盘上真的文件
    disk_file = tmp_path / "real.pdf"
    disk_file.write_bytes(b"%PDF-1.4 fake")

    fake = KbFile(
        id=uuid.uuid4(),
        kb_id=uuid.uuid4(),
        filename="real.pdf",
        file_path=str(disk_file),
        file_size=20,
        mime_type="application/pdf",
        status=FILE_STATUS_FAILED,
        progress=35,
        chunk_count=10,
        entity_count=3,
        error_message="先前错误",
        celery_task_id="old-task",
    )
    monkeypatch.setattr(
        kb_file_service, "get_file_or_raise", AsyncMock(return_value=fake)
    )
    mock_task = _patch_celery_delay(monkeypatch, task_id="new-task-id")
    db = _make_db()

    result = await kb_file_service.reindex_file(db, fake.kb_id, fake.id)
    # 状态全部重置
    assert result.status == FILE_STATUS_PENDING
    assert result.progress == 0
    assert result.chunk_count == 0
    assert result.entity_count == 0
    assert result.error_message is None
    assert result.celery_task_id == "new-task-id"
    # Celery 任务被触发
    mock_task.delay.assert_called_once()


# ──────────────── _safe_remove_disk 边界 ────────────────


def test_safe_remove_disk_missing_file_is_noop(tmp_path):
    """文件不存在不应抛错。"""
    kb_file_service._safe_remove_disk(str(tmp_path / "nope.bin"))


def test_safe_remove_disk_removes_file_and_empty_parent(tmp_path):
    parent = tmp_path / "fileid"
    parent.mkdir()
    target = parent / "x.pdf"
    target.write_bytes(b"x")
    kb_file_service._safe_remove_disk(str(target))
    # 文件 + 空目录都该被清掉
    assert not target.exists()
    assert not parent.exists()


def test_safe_remove_disk_keeps_non_empty_parent(tmp_path):
    """parent 还有其它文件 → 不应删 parent。"""
    parent = tmp_path / "fileid"
    parent.mkdir()
    target = parent / "x.pdf"
    target.write_bytes(b"x")
    sibling = parent / "y.txt"
    sibling.write_text("keep me")

    kb_file_service._safe_remove_disk(str(target))
    assert not target.exists()
    assert parent.exists()
    assert sibling.exists()
