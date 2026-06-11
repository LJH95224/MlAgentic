"""V1.5 FILE-01~05 endpoint 行为测试（mock service，CI 友好）。

验证：
- 5 个 endpoint 的请求/响应包装、Pydantic 校验、错误码
- multipart/form-data 文件上传字段名 + 状态码
- 不依赖真 PG / Milvus / Celery worker

service 层业务逻辑（磁盘写、PG 计数、Celery delay）走 service 单测 +
集成测试（S3.1.8）。
"""

import io
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api import error_codes
from app.api.deps import get_db
from app.api.exceptions import BusinessError
from app.main import app
from app.models.kb_file import (
    FILE_STATUS_COMPLETED,
    FILE_STATUS_PENDING,
    KbFile,
)


def _make_file(
    *,
    file_id=None,
    kb_id=None,
    filename="sample.pdf",
    file_size=12345,
    mime_type="application/pdf",
    status=FILE_STATUS_PENDING,
    progress=0,
    chunk_count=0,
    entity_count=0,
    error_message=None,
    celery_task_id=None,
    completed_at=None,
) -> KbFile:
    f = KbFile(
        id=file_id or uuid.uuid4(),
        kb_id=kb_id or uuid.uuid4(),
        filename=filename,
        file_path=f"/tmp/uploads/{file_id or 'x'}/{filename}",
        file_size=file_size,
        mime_type=mime_type,
        status=status,
        progress=progress,
        chunk_count=chunk_count,
        entity_count=entity_count,
        error_message=error_message,
        celery_task_id=celery_task_id,
    )
    f.created_at = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    f.completed_at = completed_at
    return f


@pytest.fixture
def client(monkeypatch):
    """TestClient + 空 DB 依赖 + 可被 monkeypatch 的 service 层。"""

    async def _empty_db():
        yield None

    app.dependency_overrides[get_db] = _empty_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


# ──────────────── FILE-01 上传 ────────────────


def test_upload_file_ok(client, monkeypatch):
    from app.services import kb_file_service

    kb_id = uuid.uuid4()
    fake = _make_file(kb_id=kb_id, celery_task_id="task-xxx")
    upload_mock = AsyncMock(return_value=fake)
    monkeypatch.setattr(kb_file_service, "upload_file", upload_mock)

    resp = client.post(
        f"/api/v1/knowledge-bases/{kb_id}/files",
        files={"file": ("sample.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["code"] == error_codes.SUCCESS
    data = body["data"]
    assert data["filename"] == "sample.pdf"
    assert data["status"] == FILE_STATUS_PENDING
    assert data["progress"] == 0
    assert data["celery_task_id"] == "task-xxx"

    # service 被以正确参数调
    upload_mock.assert_awaited_once()
    call_kwargs = upload_mock.await_args.kwargs
    assert call_kwargs["filename"] == "sample.pdf"
    assert call_kwargs["declared_mime"] == "application/pdf"


def test_upload_file_missing_file_field_rejected(client):
    """multipart 缺 file 字段 → FastAPI 422。"""
    resp = client.post(f"/api/v1/knowledge-bases/{uuid.uuid4()}/files")
    assert resp.status_code == 422
    assert resp.json()["code"] == error_codes.PARAM_INVALID


def test_upload_file_kb_not_found(client, monkeypatch):
    from app.services import kb_file_service

    kb_id = uuid.uuid4()
    monkeypatch.setattr(
        kb_file_service,
        "upload_file",
        AsyncMock(side_effect=BusinessError(error_codes.NOT_FOUND, f"知识库 {kb_id} 不存在")),
    )

    resp = client.post(
        f"/api/v1/knowledge-bases/{kb_id}/files",
        files={"file": ("a.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == error_codes.NOT_FOUND


def test_upload_file_unsupported_format_returns_415(client, monkeypatch):
    from app.services import kb_file_service

    monkeypatch.setattr(
        kb_file_service,
        "upload_file",
        AsyncMock(
            side_effect=BusinessError(
                error_codes.UNSUPPORTED_MEDIA, "不支持的文件格式: a.xls"
            )
        ),
    )

    resp = client.post(
        f"/api/v1/knowledge-bases/{uuid.uuid4()}/files",
        files={"file": ("a.xls", b"x", "application/vnd.ms-excel")},
    )
    assert resp.status_code == 415
    assert resp.json()["code"] == error_codes.UNSUPPORTED_MEDIA


def test_upload_file_too_large_returns_413(client, monkeypatch):
    from app.services import kb_file_service

    monkeypatch.setattr(
        kb_file_service,
        "upload_file",
        AsyncMock(
            side_effect=BusinessError(
                error_codes.FILE_TOO_LARGE, "文件大小超出限制 50 MB"
            )
        ),
    )

    resp = client.post(
        f"/api/v1/knowledge-bases/{uuid.uuid4()}/files",
        files={"file": ("big.pdf", b"x" * 1024, "application/pdf")},
    )
    assert resp.status_code == 413
    assert resp.json()["code"] == error_codes.FILE_TOO_LARGE


def test_upload_file_bad_kb_uuid(client):
    resp = client.post(
        "/api/v1/knowledge-bases/not-a-uuid/files",
        files={"file": ("a.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 422


# ──────────────── FILE-02 列表 ────────────────


def test_list_files_default_pagination(client, monkeypatch):
    from app.services import kb_file_service

    kb_id = uuid.uuid4()
    items = [
        _make_file(kb_id=kb_id, filename="a.pdf", chunk_count=10),
        _make_file(kb_id=kb_id, filename="b.docx", status=FILE_STATUS_COMPLETED, progress=100),
    ]
    monkeypatch.setattr(
        kb_file_service,
        "list_kb_files",
        AsyncMock(return_value=(items, 7)),
    )

    resp = client.get(f"/api/v1/knowledge-bases/{kb_id}/files")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["total"] == 7
    assert len(data["items"]) == 2
    # 列表项不含 error_message / celery_task_id（详情才给）
    first = data["items"][0]
    assert "error_message" not in first
    assert "celery_task_id" not in first
    assert first["filename"] == "a.pdf"
    assert first["chunk_count"] == 10


def test_list_files_explicit_pagination(client, monkeypatch):
    from app.services import kb_file_service

    list_mock = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(kb_file_service, "list_kb_files", list_mock)

    resp = client.get(
        f"/api/v1/knowledge-bases/{uuid.uuid4()}/files?page=2&page_size=5"
    )
    assert resp.status_code == 200
    assert list_mock.await_args.kwargs == {"page": 2, "page_size": 5}


def test_list_files_kb_not_found(client, monkeypatch):
    from app.services import kb_file_service

    kb_id = uuid.uuid4()
    monkeypatch.setattr(
        kb_file_service,
        "list_kb_files",
        AsyncMock(side_effect=BusinessError(error_codes.NOT_FOUND, "知识库不存在")),
    )

    resp = client.get(f"/api/v1/knowledge-bases/{kb_id}/files")
    assert resp.status_code == 404


def test_list_files_page_size_too_large(client):
    resp = client.get(
        f"/api/v1/knowledge-bases/{uuid.uuid4()}/files?page_size=200"
    )
    assert resp.status_code == 422


# ──────────────── FILE-03 详情 ────────────────


def test_get_file_detail_ok(client, monkeypatch):
    from app.services import kb_file_service

    kb_id = uuid.uuid4()
    file_id = uuid.uuid4()
    fake = _make_file(
        file_id=file_id,
        kb_id=kb_id,
        status=FILE_STATUS_COMPLETED,
        progress=100,
        chunk_count=42,
        entity_count=10,
        celery_task_id="task-yyy",
        completed_at=datetime(2026, 6, 11, 12, 5, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        kb_file_service, "get_file_or_raise", AsyncMock(return_value=fake)
    )

    resp = client.get(f"/api/v1/knowledge-bases/{kb_id}/files/{file_id}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == str(file_id)
    assert data["status"] == FILE_STATUS_COMPLETED
    assert data["progress"] == 100
    assert data["chunk_count"] == 42
    assert data["entity_count"] == 10
    assert data["celery_task_id"] == "task-yyy"
    assert data["completed_at"].startswith("2026-06-11T12:05:00")
    # error_message 字段存在但为 null
    assert data["error_message"] is None


def test_get_file_detail_with_error_message(client, monkeypatch):
    """failed 状态的文件应在详情里返回 error_message。"""
    from app.services import kb_file_service
    from app.models.kb_file import FILE_STATUS_FAILED

    kb_id = uuid.uuid4()
    file_id = uuid.uuid4()
    fake = _make_file(
        file_id=file_id,
        kb_id=kb_id,
        status=FILE_STATUS_FAILED,
        progress=35,
        error_message="向量维度不匹配，期望 4096 得到 1024",
    )
    monkeypatch.setattr(
        kb_file_service, "get_file_or_raise", AsyncMock(return_value=fake)
    )

    resp = client.get(f"/api/v1/knowledge-bases/{kb_id}/files/{file_id}")
    data = resp.json()["data"]
    assert data["status"] == FILE_STATUS_FAILED
    assert "向量维度" in data["error_message"]


def test_get_file_detail_not_found(client, monkeypatch):
    from app.services import kb_file_service

    kb_id = uuid.uuid4()
    file_id = uuid.uuid4()
    monkeypatch.setattr(
        kb_file_service,
        "get_file_or_raise",
        AsyncMock(side_effect=BusinessError(error_codes.NOT_FOUND, "文件不存在")),
    )

    resp = client.get(f"/api/v1/knowledge-bases/{kb_id}/files/{file_id}")
    assert resp.status_code == 404


def test_get_file_detail_bad_uuid(client):
    resp = client.get(
        f"/api/v1/knowledge-bases/{uuid.uuid4()}/files/not-a-uuid"
    )
    assert resp.status_code == 422


# ──────────────── FILE-04 删除 ────────────────


def test_delete_file_ok(client, monkeypatch):
    from app.services import kb_file_service

    delete_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(kb_file_service, "delete_file", delete_mock)

    resp = client.delete(
        f"/api/v1/knowledge-bases/{uuid.uuid4()}/files/{uuid.uuid4()}"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == error_codes.SUCCESS
    assert body["data"] is None
    delete_mock.assert_awaited_once()


def test_delete_file_not_found(client, monkeypatch):
    from app.services import kb_file_service

    monkeypatch.setattr(
        kb_file_service,
        "delete_file",
        AsyncMock(side_effect=BusinessError(error_codes.NOT_FOUND, "文件不存在")),
    )

    resp = client.delete(
        f"/api/v1/knowledge-bases/{uuid.uuid4()}/files/{uuid.uuid4()}"
    )
    assert resp.status_code == 404


# ──────────────── FILE-05 重新入库 ────────────────


def test_reindex_file_ok(client, monkeypatch):
    from app.services import kb_file_service

    kb_id = uuid.uuid4()
    file_id = uuid.uuid4()
    fake = _make_file(
        file_id=file_id,
        kb_id=kb_id,
        status=FILE_STATUS_PENDING,
        progress=0,
        celery_task_id="task-reindex-zzz",
    )
    reindex_mock = AsyncMock(return_value=fake)
    monkeypatch.setattr(kb_file_service, "reindex_file", reindex_mock)

    resp = client.post(
        f"/api/v1/knowledge-bases/{kb_id}/files/{file_id}/reindex"
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == FILE_STATUS_PENDING
    assert data["progress"] == 0
    assert data["celery_task_id"] == "task-reindex-zzz"


def test_reindex_file_disk_missing_returns_404(client, monkeypatch):
    from app.services import kb_file_service

    monkeypatch.setattr(
        kb_file_service,
        "reindex_file",
        AsyncMock(
            side_effect=BusinessError(
                error_codes.NOT_FOUND,
                "文件磁盘副本已丢失，请重新上传 file_id=xxx",
            )
        ),
    )

    resp = client.post(
        f"/api/v1/knowledge-bases/{uuid.uuid4()}/files/{uuid.uuid4()}/reindex"
    )
    assert resp.status_code == 404
    assert "重新上传" in resp.json()["message"]


def test_reindex_file_kb_or_file_not_found(client, monkeypatch):
    from app.services import kb_file_service

    monkeypatch.setattr(
        kb_file_service,
        "reindex_file",
        AsyncMock(side_effect=BusinessError(error_codes.NOT_FOUND, "文件不存在")),
    )

    resp = client.post(
        f"/api/v1/knowledge-bases/{uuid.uuid4()}/files/{uuid.uuid4()}/reindex"
    )
    assert resp.status_code == 404
