"""S1.0b 改造的端到端验证（mock 掉 DB / 服务层，专测统一响应格式）。

V1.0 原 `tests/test_sessions_api.py` / `tests/test_chat_stream.py` 全部依赖真 PG
（`@skip_without_db` 装饰），CI 上默认 skip，无法保证统一响应格式改造没回退。

本文件用 FastAPI 的 `dependency_overrides` + monkeypatch service 层，跑一个真实的
ASGI app（注册了 V1.5 handler + 真实的 endpoint 代码），但底层不连 DB。专攻：

- POST /api/v1/sessions 成功 → 响应包成 ApiResponse[CreateSessionResponse]
- POST /api/v1/chat/stream session 不存在 → ApiResponse(40400) JSON
- POST /api/v1/chat/stream 缺字段 → ApiResponse(40001) JSON
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import error_codes
from app.api.deps import get_db
from app.main import app
from app.models.session import ChatSession


@pytest.fixture
def client_no_db(monkeypatch):
    """提供一个 TestClient，DB 依赖被替换为空 stub，service 层按需 monkeypatch。

    注意：lifespan 不启动（TestClient 默认不跑 lifespan），所以不会触发
    Milvus / Neo4j 的初始化，CI 友好。
    """
    # 替换 DB 依赖：endpoint 拿到 None 也无所谓（service 层会被 mock 掉）
    async def _empty_db():  # noqa: D401
        yield None

    app.dependency_overrides[get_db] = _empty_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


# ───────── POST /sessions ─────────


def test_create_session_returns_unified_response(client_no_db, monkeypatch):
    """POST /api/v1/sessions 必须返回 ApiResponse{code:0, data:{id, ..., created_at}}。

    V1.5 SES-01 起 data 是完整 SessionDetail，含 title/summary/message_count/updated_at；
    本测试只校验顶层包装与 id/created_at 序列化，详细字段由 test_sessions_v1_5_endpoints 覆盖。
    """
    fake_id = uuid4()
    fake_now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    fake_session = ChatSession(
        id=fake_id, title=None, summary=None, message_count=0
    )
    # server_default / DB 才会填的字段，单测里手动塞
    fake_session.created_at = fake_now
    fake_session.updated_at = fake_now
    fake_session.summarized_at = None
    fake_session.db_metadata = None

    # service 层替换为 AsyncMock，避免真连库
    from app.services import session_service

    monkeypatch.setattr(
        session_service,
        "create_session",
        AsyncMock(return_value=fake_session),
    )

    resp = client_no_db.post("/api/v1/sessions")
    assert resp.status_code == 201
    body = resp.json()

    # 顶层包装
    assert body["code"] == error_codes.SUCCESS
    assert body["message"] == "success"

    # data 字段结构（V1.5 详尽字段在 test_sessions_v1_5_endpoints 覆盖）
    data = body["data"]
    assert data["id"] == str(fake_id)
    # ISO 8601 时间戳序列化
    assert data["created_at"].startswith("2026-06-11T12:00:00")


# ───────── POST /chat/stream 错误路径 ─────────


def test_chat_stream_missing_session_returns_business_404(client_no_db, monkeypatch):
    """会话不存在 → 业务码 40400（HTTP 404），body 是 ApiResponse JSON 而非 SSE。"""
    from app.services import session_service

    monkeypatch.setattr(
        session_service,
        "get_session",
        AsyncMock(return_value=None),
    )

    bogus_id = str(uuid4())
    resp = client_no_db.post(
        "/api/v1/chat/stream",
        json={"session_id": bogus_id, "content": "hi"},
    )

    assert resp.status_code == 404
    # 关键：不是 SSE 流
    assert resp.headers["content-type"].startswith("application/json")

    body = resp.json()
    assert body["code"] == error_codes.NOT_FOUND
    assert body["data"] is None
    # message 应该提到具体 session_id，便于排错
    assert bogus_id in body["message"]


def test_chat_stream_validation_failure_returns_business_40001(client_no_db):
    """缺 content 字段 → Pydantic 校验失败 → 业务码 40001（HTTP 422），统一 JSON。"""
    resp = client_no_db.post(
        "/api/v1/chat/stream",
        json={"session_id": str(uuid4())},  # 缺 content
    )

    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == error_codes.PARAM_INVALID
    assert body["data"] is None
    # message 至少包含 content 字段名
    assert "content" in body["message"].lower()


def test_chat_stream_empty_content_returns_business_40001(client_no_db):
    """content 空字符串 → min_length=1 校验失败 → 业务码 40001。"""
    resp = client_no_db.post(
        "/api/v1/chat/stream",
        json={"session_id": str(uuid4()), "content": ""},
    )

    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == error_codes.PARAM_INVALID


def test_chat_stream_bad_uuid_returns_business_40001(client_no_db):
    """session_id 非 UUID → Pydantic 校验失败 → 业务码 40001。"""
    resp = client_no_db.post(
        "/api/v1/chat/stream",
        json={"session_id": "not-a-uuid", "content": "hi"},
    )

    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == error_codes.PARAM_INVALID
