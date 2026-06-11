"""V1.5 SES-01~06 endpoint 行为测试（不依赖真 DB；mock service 层）。

验证：
- endpoint 是否正确包装响应为 ApiResponse[T]
- Pydantic 请求 Schema 的校验失败 → 40001
- BusinessError(NOT_FOUND) → 40400 + HTTP 404
- 各 endpoint 的 HTTP 状态码、必填参数

service 层业务逻辑（如 list_sessions 排序、游标翻页正确性）走集成测试：
[tests/test_sessions_v1_5_integration.py]
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import error_codes
from app.api.deps import get_db
from app.api.exceptions import BusinessError
from app.main import app
from app.models.message import ChatMessage
from app.models.session import ChatSession


def _make_session(
    *, sid=None, title="测试会话", message_count=0, summary=None
) -> ChatSession:
    """构造一个未连库的 ChatSession ORM 实例，给 mock 返回用。"""
    now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    s = ChatSession(
        id=sid or uuid4(),
        title=title,
        summary=summary,
        message_count=message_count,
    )
    # 这些字段由 server_default / 写库时填充，单测里手动塞
    s.created_at = now
    s.updated_at = now
    s.summarized_at = None
    s.db_metadata = None
    return s


def _make_message(*, role="user", content="hi", session_id=None) -> ChatMessage:
    m = ChatMessage(
        id=uuid4(),
        session_id=session_id or uuid4(),
        role=role,
        content=content,
        tool_calls=None,
    )
    m.created_at = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    return m


@pytest.fixture
def client(monkeypatch):
    """提供 TestClient + 空 DB 依赖 + 可被 monkeypatch 的 service 层。"""

    async def _empty_db():
        yield None

    app.dependency_overrides[get_db] = _empty_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


# ──────────────── SES-01 创建 ────────────────


def test_create_session_no_body(client, monkeypatch):
    """不传 body → title=null，调 create_session(title=None)。"""
    from app.services import session_service

    fake = _make_session(title=None)
    create_mock = AsyncMock(return_value=fake)
    monkeypatch.setattr(session_service, "create_session", create_mock)

    resp = client.post("/api/v1/sessions")

    assert resp.status_code == 201
    body = resp.json()
    assert body["code"] == error_codes.SUCCESS
    data = body["data"]
    assert data["id"] == str(fake.id)
    assert data["title"] is None
    assert data["summary"] is None
    assert data["message_count"] == 0
    # service 应被以 title=None 调用
    create_mock.assert_awaited_once()
    assert create_mock.await_args.kwargs.get("title") is None


def test_create_session_with_title(client, monkeypatch):
    from app.services import session_service

    fake = _make_session(title="台风专题")
    create_mock = AsyncMock(return_value=fake)
    monkeypatch.setattr(session_service, "create_session", create_mock)

    resp = client.post("/api/v1/sessions", json={"title": "台风专题"})

    assert resp.status_code == 201
    assert resp.json()["data"]["title"] == "台风专题"
    assert create_mock.await_args.kwargs["title"] == "台风专题"


def test_create_session_blank_title_rejected(client):
    """空白 title 被 SessionCreateRequest 拦截。"""
    resp = client.post("/api/v1/sessions", json={"title": "   "})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == error_codes.PARAM_INVALID


def test_create_session_overlong_title_rejected(client):
    """超 100 字的 title 被 max_length 拦截。"""
    resp = client.post("/api/v1/sessions", json={"title": "x" * 101})
    assert resp.status_code == 422
    assert resp.json()["code"] == error_codes.PARAM_INVALID


# ──────────────── SES-02 列表 ────────────────


def test_list_sessions_default_pagination(client, monkeypatch):
    from app.services import session_service

    fake_items = [
        _make_session(title="t1", message_count=3, summary="a" * 200),
        _make_session(title="t2", message_count=0),
    ]
    list_mock = AsyncMock(return_value=(fake_items, 25))
    monkeypatch.setattr(session_service, "list_sessions", list_mock)

    resp = client.get("/api/v1/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == error_codes.SUCCESS
    data = body["data"]
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["total"] == 25
    assert len(data["items"]) == 2
    # summary_snippet 应是前 80 字截断
    first = data["items"][0]
    assert first["title"] == "t1"
    assert first["message_count"] == 3
    assert first["summary_snippet"] == "a" * 80
    # 第二条 summary 为 null
    assert data["items"][1]["summary_snippet"] is None


def test_list_sessions_explicit_pagination(client, monkeypatch):
    from app.services import session_service

    list_mock = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(session_service, "list_sessions", list_mock)

    resp = client.get("/api/v1/sessions?page=3&page_size=5")
    assert resp.status_code == 200
    list_mock.assert_awaited_once()
    assert list_mock.await_args.kwargs == {"page": 3, "page_size": 5}


def test_list_sessions_page_size_too_large_rejected(client):
    resp = client.get("/api/v1/sessions?page_size=200")
    assert resp.status_code == 422
    assert resp.json()["code"] == error_codes.PARAM_INVALID


def test_list_sessions_page_zero_rejected(client):
    resp = client.get("/api/v1/sessions?page=0")
    assert resp.status_code == 422
    assert resp.json()["code"] == error_codes.PARAM_INVALID


# ──────────────── SES-03 详情 ────────────────


def test_get_session_detail_ok(client, monkeypatch):
    from app.services import session_service

    sid = uuid4()
    fake = _make_session(sid=sid, title="详情测试", message_count=7)
    monkeypatch.setattr(
        session_service,
        "get_session_or_raise",
        AsyncMock(return_value=fake),
    )

    resp = client.get(f"/api/v1/sessions/{sid}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == str(sid)
    assert data["title"] == "详情测试"
    assert data["message_count"] == 7
    # SES-03 要求详情含 summary 等完整字段（即使为 null）
    assert "summary" in data
    assert "summarized_at" in data
    assert "metadata" in data


def test_get_session_detail_not_found(client, monkeypatch):
    from app.services import session_service

    sid = uuid4()
    monkeypatch.setattr(
        session_service,
        "get_session_or_raise",
        AsyncMock(side_effect=BusinessError(error_codes.NOT_FOUND, f"会话 {sid} 不存在")),
    )

    resp = client.get(f"/api/v1/sessions/{sid}")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == error_codes.NOT_FOUND
    assert str(sid) in body["message"]


def test_get_session_detail_bad_uuid(client):
    resp = client.get("/api/v1/sessions/not-a-uuid")
    assert resp.status_code == 422
    assert resp.json()["code"] == error_codes.PARAM_INVALID


# ──────────────── SES-04 改标题 ────────────────


def test_update_session_title_ok(client, monkeypatch):
    from app.services import session_service

    sid = uuid4()
    fake = _make_session(sid=sid, title="新标题")
    update_mock = AsyncMock(return_value=fake)
    monkeypatch.setattr(session_service, "update_session_title", update_mock)

    resp = client.patch(f"/api/v1/sessions/{sid}", json={"title": "新标题"})
    assert resp.status_code == 200
    assert resp.json()["data"]["title"] == "新标题"
    update_mock.assert_awaited_once()
    assert update_mock.await_args.kwargs == {"title": "新标题"}


def test_update_session_title_empty_rejected(client):
    resp = client.patch(f"/api/v1/sessions/{uuid4()}", json={"title": ""})
    assert resp.status_code == 422
    assert resp.json()["code"] == error_codes.PARAM_INVALID


def test_update_session_title_overlong_rejected(client):
    resp = client.patch(f"/api/v1/sessions/{uuid4()}", json={"title": "x" * 101})
    assert resp.status_code == 422


def test_update_session_extra_fields_rejected(client):
    """PRD 明确：仅 title 字段可改；传 summary 等其他字段应被拦截。"""
    resp = client.patch(
        f"/api/v1/sessions/{uuid4()}",
        json={"title": "ok", "summary": "尝试改摘要"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == error_codes.PARAM_INVALID
    # 错误信息提到 summary 字段非法
    assert "summary" in body["message"].lower() or "extra" in body["message"].lower()


def test_update_session_missing_title_rejected(client):
    resp = client.patch(f"/api/v1/sessions/{uuid4()}", json={})
    assert resp.status_code == 422


def test_update_session_not_found(client, monkeypatch):
    from app.services import session_service

    sid = uuid4()
    monkeypatch.setattr(
        session_service,
        "update_session_title",
        AsyncMock(
            side_effect=BusinessError(error_codes.NOT_FOUND, f"会话 {sid} 不存在")
        ),
    )

    resp = client.patch(f"/api/v1/sessions/{sid}", json={"title": "x"})
    assert resp.status_code == 404
    assert resp.json()["code"] == error_codes.NOT_FOUND


# ──────────────── SES-05 删除 ────────────────


def test_delete_session_ok(client, monkeypatch):
    from app.services import session_service

    sid = uuid4()
    delete_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(session_service, "delete_session", delete_mock)

    resp = client.delete(f"/api/v1/sessions/{sid}")
    assert resp.status_code == 200  # 走统一响应 200 + code:0；不是 204
    body = resp.json()
    assert body["code"] == error_codes.SUCCESS
    assert body["data"] is None
    delete_mock.assert_awaited_once()


def test_delete_session_not_found(client, monkeypatch):
    from app.services import session_service

    sid = uuid4()
    monkeypatch.setattr(
        session_service,
        "delete_session",
        AsyncMock(
            side_effect=BusinessError(error_codes.NOT_FOUND, f"会话 {sid} 不存在")
        ),
    )

    resp = client.delete(f"/api/v1/sessions/{sid}")
    assert resp.status_code == 404
    assert resp.json()["code"] == error_codes.NOT_FOUND


# ──────────────── SES-06 消息历史 ────────────────


def test_list_messages_default(client, monkeypatch):
    from app.services import session_service

    sid = uuid4()
    msgs = [
        _make_message(role="user", content="问题1", session_id=sid),
        _make_message(role="assistant", content="回答1", session_id=sid),
    ]
    list_mock = AsyncMock(return_value=(msgs, False, msgs[0].id))
    monkeypatch.setattr(session_service, "list_session_messages", list_mock)

    resp = client.get(f"/api/v1/sessions/{sid}/messages")
    assert resp.status_code == 200
    body = resp.json()
    data = body["data"]
    assert len(data["items"]) == 2
    assert data["has_more"] is False
    assert data["next_before"] == str(msgs[0].id)
    # 默认 limit=20
    assert list_mock.await_args.kwargs == {"limit": 20, "before": None}


def test_list_messages_with_cursor(client, monkeypatch):
    from app.services import session_service

    sid = uuid4()
    before_id = uuid4()
    list_mock = AsyncMock(return_value=([], False, None))
    monkeypatch.setattr(session_service, "list_session_messages", list_mock)

    resp = client.get(
        f"/api/v1/sessions/{sid}/messages?limit=50&before={before_id}"
    )
    assert resp.status_code == 200
    assert list_mock.await_args.kwargs == {"limit": 50, "before": before_id}


def test_list_messages_limit_too_large_rejected(client):
    resp = client.get(f"/api/v1/sessions/{uuid4()}/messages?limit=200")
    assert resp.status_code == 422
    assert resp.json()["code"] == error_codes.PARAM_INVALID


def test_list_messages_invalid_before_rejected(client):
    resp = client.get(f"/api/v1/sessions/{uuid4()}/messages?before=not-a-uuid")
    assert resp.status_code == 422


def test_list_messages_session_not_found(client, monkeypatch):
    from app.services import session_service

    sid = uuid4()
    monkeypatch.setattr(
        session_service,
        "list_session_messages",
        AsyncMock(side_effect=BusinessError(error_codes.NOT_FOUND, f"会话 {sid} 不存在")),
    )

    resp = client.get(f"/api/v1/sessions/{sid}/messages")
    assert resp.status_code == 404
    assert resp.json()["code"] == error_codes.NOT_FOUND


def test_list_messages_invalid_cursor_returns_40001(client, monkeypatch):
    from app.services import session_service

    sid = uuid4()
    before = uuid4()
    monkeypatch.setattr(
        session_service,
        "list_session_messages",
        AsyncMock(
            side_effect=BusinessError(
                error_codes.PARAM_INVALID,
                f"游标消息 {before} 不存在或不属于会话 {sid}",
            )
        ),
    )

    resp = client.get(f"/api/v1/sessions/{sid}/messages?before={before}")
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == error_codes.PARAM_INVALID
