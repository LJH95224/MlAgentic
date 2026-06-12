"""V1.5 S4 SES-07 / SES-08 endpoint + chat_service 触发逻辑测试。

不依赖真 PG / Celery worker；mock 掉 session_service / Celery delay 验证：
- POST /sessions/{id}/summarize: 202 + task_id / 404 / Celery 不可达
- chat_service._maybe_trigger_title_task: 触发条件判断（title 已存在 / msg_count 不等于 2）
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api import error_codes
from app.api.deps import get_db
from app.api.exceptions import BusinessError
from app.main import app
from app.models.session import ChatSession


def _make_session(*, sid=None, title=None, message_count=2):
    s = ChatSession(
        id=sid or uuid.uuid4(),
        title=title,
        summary=None,
        message_count=message_count,
    )
    now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    s.created_at = now
    s.updated_at = now
    return s


@pytest.fixture
def client(monkeypatch):
    async def _empty_db():
        yield None

    app.dependency_overrides[get_db] = _empty_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


# ──────────────── SES-08 endpoint ────────────────


def test_summarize_endpoint_returns_202_with_task_id(client, monkeypatch):
    from app.services import session_service

    sid = uuid.uuid4()
    fake = _make_session(sid=sid)
    monkeypatch.setattr(
        session_service, "get_session_or_raise", AsyncMock(return_value=fake)
    )

    # mock Celery .delay() 返一个有 .id 的对象
    mock_task = MagicMock()
    mock_task.delay = MagicMock(return_value=MagicMock(id="task-summary-zzz"))

    import app.tasks.session_task as mod

    monkeypatch.setattr(mod, "generate_session_summary_task", mock_task)

    resp = client.post(f"/api/v1/sessions/{sid}/summarize")
    assert resp.status_code == 202
    body = resp.json()
    assert body["code"] == error_codes.SUCCESS
    assert body["message"] == "摘要生成任务已提交"
    assert body["data"]["task_id"] == "task-summary-zzz"
    mock_task.delay.assert_called_once_with(str(sid))


def test_summarize_endpoint_session_not_found(client, monkeypatch):
    from app.services import session_service

    sid = uuid.uuid4()
    monkeypatch.setattr(
        session_service,
        "get_session_or_raise",
        AsyncMock(side_effect=BusinessError(error_codes.NOT_FOUND, f"会话 {sid} 不存在")),
    )

    resp = client.post(f"/api/v1/sessions/{sid}/summarize")
    assert resp.status_code == 404
    assert resp.json()["code"] == error_codes.NOT_FOUND


def test_summarize_endpoint_celery_unavailable_returns_50300(client, monkeypatch):
    from app.services import session_service

    sid = uuid.uuid4()
    monkeypatch.setattr(
        session_service, "get_session_or_raise", AsyncMock(return_value=_make_session(sid=sid))
    )

    mock_task = MagicMock()
    mock_task.delay = MagicMock(side_effect=RuntimeError("broker down"))
    import app.tasks.session_task as mod

    monkeypatch.setattr(mod, "generate_session_summary_task", mock_task)

    resp = client.post(f"/api/v1/sessions/{sid}/summarize")
    assert resp.status_code == 503
    assert resp.json()["code"] == error_codes.CELERY_UNAVAILABLE


def test_summarize_endpoint_bad_uuid(client):
    resp = client.post("/api/v1/sessions/not-a-uuid/summarize")
    assert resp.status_code == 422


# ──────────────── chat_service._maybe_trigger_title_task ────────────────


def _patch_db_session_query(monkeypatch, return_value):
    """让 db.execute(select(ChatSession)) 返指定值。"""
    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=return_value)
    mock_db.execute.return_value = mock_result
    return mock_db


async def test_maybe_trigger_title_skips_when_title_already_set(monkeypatch):
    from app.services import chat_service

    sess = _make_session(title="手动标题", message_count=2)
    mock_db = _patch_db_session_query(monkeypatch, sess)

    mock_task = MagicMock()
    mock_task.delay = MagicMock()
    import app.tasks.session_task as mod

    monkeypatch.setattr(mod, "generate_session_title_task", mock_task)

    await chat_service._maybe_trigger_title_task(mock_db, uuid.uuid4())
    mock_task.delay.assert_not_called()


async def test_maybe_trigger_title_skips_when_message_count_not_2(monkeypatch):
    from app.services import chat_service

    # 4 条消息（已不是首轮）
    sess = _make_session(title=None, message_count=4)
    mock_db = _patch_db_session_query(monkeypatch, sess)

    mock_task = MagicMock()
    mock_task.delay = MagicMock()
    import app.tasks.session_task as mod

    monkeypatch.setattr(mod, "generate_session_title_task", mock_task)

    await chat_service._maybe_trigger_title_task(mock_db, uuid.uuid4())
    mock_task.delay.assert_not_called()


async def test_maybe_trigger_title_fires_when_first_round_and_no_title(monkeypatch):
    from app.services import chat_service

    sess = _make_session(title=None, message_count=2)
    mock_db = _patch_db_session_query(monkeypatch, sess)

    mock_task = MagicMock()
    mock_task.delay = MagicMock(return_value=MagicMock(id="task-title-aaa"))
    import app.tasks.session_task as mod

    monkeypatch.setattr(mod, "generate_session_title_task", mock_task)

    sid = uuid.uuid4()
    await chat_service._maybe_trigger_title_task(mock_db, sid)
    mock_task.delay.assert_called_once_with(str(sid))


async def test_maybe_trigger_title_celery_failure_does_not_raise(monkeypatch):
    """Celery 失败仅记 warning 不抛错（不阻断对话主路径）。"""
    from app.services import chat_service

    sess = _make_session(title=None, message_count=2)
    mock_db = _patch_db_session_query(monkeypatch, sess)

    mock_task = MagicMock()
    mock_task.delay = MagicMock(side_effect=RuntimeError("broker down"))
    import app.tasks.session_task as mod

    monkeypatch.setattr(mod, "generate_session_title_task", mock_task)

    # 不抛即视为通过
    await chat_service._maybe_trigger_title_task(mock_db, uuid.uuid4())


async def test_maybe_trigger_title_session_not_found_does_not_raise(monkeypatch):
    from app.services import chat_service

    mock_db = _patch_db_session_query(monkeypatch, None)

    mock_task = MagicMock()
    mock_task.delay = MagicMock()
    import app.tasks.session_task as mod

    monkeypatch.setattr(mod, "generate_session_title_task", mock_task)

    await chat_service._maybe_trigger_title_task(mock_db, uuid.uuid4())
    mock_task.delay.assert_not_called()
