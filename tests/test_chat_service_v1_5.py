"""V1.5 SES-09 上下文窗口截断 + 消息计数维护单测。

不依赖真 DB；用 AsyncMock 替换 db.execute，验：
- system 消息全量、非 system 只取最近 N 条
- N 由 settings.context_window_messages 决定（边界 1 / 大窗口）
- _append_message 写消息 + 执行一条 UPDATE 维护 message_count + updated_at
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.message import ChatMessage
from app.models.session import ChatSession
from app.services import chat_service


def _msg(role: str, content: str, *, at: datetime) -> ChatMessage:
    """构造一个未连库的 ChatMessage（手动设 created_at）。"""
    m = ChatMessage(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        role=role,
        content=content,
    )
    m.created_at = at
    return m


def _make_fake_db(*, sys_rows, non_sys_rows, total_count):
    """构造一个 AsyncMock db，按 execute() 调用顺序返回不同结果。

    _load_history 内部按顺序发 3 次 execute：
      1) system 全量（正序）
      2) 非 system 最近 N 条（按 desc 排序）
      3) 总数 count（_count_messages）
    """
    db = MagicMock()

    sys_result = MagicMock()
    sys_result.scalars.return_value.all.return_value = sys_rows

    non_sys_result = MagicMock()
    # 真实 SQL 是 desc 排序；mock 时把 non_sys_rows 倒序塞进去
    non_sys_result.scalars.return_value.all.return_value = list(reversed(non_sys_rows))

    count_result = MagicMock()
    count_result.scalar_one.return_value = total_count

    db.execute = AsyncMock(side_effect=[sys_result, non_sys_result, count_result])
    return db


@pytest.fixture(autouse=True)
def _reset_settings_for_each(monkeypatch):
    """每个测试单独设 context window；防互相污染。"""
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ──────────────── _load_history 行为 ────────────────


async def test_load_history_returns_all_when_within_window(monkeypatch):
    """非 system 消息数 ≤ 窗口 → 全部返回。"""
    monkeypatch.setenv("CONTEXT_WINDOW_MESSAGES", "20")

    now = datetime.now(timezone.utc)
    sys_rows = [_msg("system", "sys-prompt", at=now - timedelta(minutes=10))]
    non_sys_rows = [
        _msg("user", "u1", at=now - timedelta(minutes=5)),
        _msg("assistant", "a1", at=now - timedelta(minutes=4)),
        _msg("user", "u2", at=now - timedelta(minutes=3)),
        _msg("assistant", "a2", at=now - timedelta(minutes=2)),
    ]
    db = _make_fake_db(sys_rows=sys_rows, non_sys_rows=non_sys_rows, total_count=5)

    history = await chat_service._load_history(db, uuid.uuid4())
    contents = [h["content"] for h in history]
    # system 在最前，其余按 created_at 正序
    assert contents == ["sys-prompt", "u1", "a1", "u2", "a2"]


async def test_load_history_truncates_when_over_window(monkeypatch):
    """非 system 消息数 > 窗口 → 只保留最近 N 条；system 仍然全部包含。"""
    monkeypatch.setenv("CONTEXT_WINDOW_MESSAGES", "3")

    now = datetime.now(timezone.utc)
    sys_rows = [_msg("system", "sys-prompt", at=now - timedelta(minutes=10))]
    # 模拟 service 已经按 desc 拿了最近 3 条；service 内部会 reverse 成正序
    non_sys_rows = [
        _msg("user", "u3", at=now - timedelta(minutes=3)),
        _msg("assistant", "a3", at=now - timedelta(minutes=2)),
        _msg("user", "u4", at=now - timedelta(minutes=1)),
    ]
    db = _make_fake_db(sys_rows=sys_rows, non_sys_rows=non_sys_rows, total_count=10)

    history = await chat_service._load_history(db, uuid.uuid4())
    contents = [h["content"] for h in history]
    # system 始终包含；非 system 只剩窗口大小 3 条；按正序
    assert contents == ["sys-prompt", "u3", "a3", "u4"]


async def test_load_history_system_does_not_count_toward_window(monkeypatch):
    """有多条 system 时也全部包含；不挤占非 system 窗口。"""
    monkeypatch.setenv("CONTEXT_WINDOW_MESSAGES", "2")

    now = datetime.now(timezone.utc)
    sys_rows = [
        _msg("system", "s1", at=now - timedelta(minutes=10)),
        _msg("system", "s2", at=now - timedelta(minutes=9)),
    ]
    non_sys_rows = [
        _msg("user", "u1", at=now - timedelta(minutes=2)),
        _msg("assistant", "a1", at=now - timedelta(minutes=1)),
    ]
    db = _make_fake_db(sys_rows=sys_rows, non_sys_rows=non_sys_rows, total_count=4)

    history = await chat_service._load_history(db, uuid.uuid4())
    roles = [h["role"] for h in history]
    contents = [h["content"] for h in history]
    # 2 条 system + 2 条非 system，全部就位
    assert roles == ["system", "system", "user", "assistant"]
    assert contents == ["s1", "s2", "u1", "a1"]


async def test_load_history_preserves_tool_calls(monkeypatch):
    """assistant 消息带 tool_calls 时，结构应原样透传给 Agent。"""
    monkeypatch.setenv("CONTEXT_WINDOW_MESSAGES", "20")

    now = datetime.now(timezone.utc)
    ai = _msg("assistant", None, at=now - timedelta(minutes=2))
    ai.tool_calls = [{"id": "1", "name": "search", "args": {"q": "x"}}]
    non_sys_rows = [ai]
    db = _make_fake_db(sys_rows=[], non_sys_rows=non_sys_rows, total_count=1)

    history = await chat_service._load_history(db, uuid.uuid4())
    assert len(history) == 1
    assert history[0]["tool_calls"] == [{"id": "1", "name": "search", "args": {"q": "x"}}]


async def test_load_history_window_clamped_to_at_least_1(monkeypatch):
    """窗口配置成 0 / 负数 → 至少取 1 条非 system（防退化为空上下文）。"""
    monkeypatch.setenv("CONTEXT_WINDOW_MESSAGES", "0")

    now = datetime.now(timezone.utc)
    non_sys_rows = [_msg("user", "only", at=now)]
    db = _make_fake_db(sys_rows=[], non_sys_rows=non_sys_rows, total_count=1)

    history = await chat_service._load_history(db, uuid.uuid4())
    assert len(history) == 1
    assert history[0]["content"] == "only"


# ──────────────── _append_message 行为 ────────────────


async def test_append_message_inserts_and_bumps_counters():
    """写消息 + 触发一条 UPDATE ChatSession SET message_count = +1, updated_at = now()。"""
    db = MagicMock()
    db.add = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    sid = uuid.uuid4()
    msg = await chat_service._append_message(
        db, sid, role="user", content="hi"
    )

    # 写了一条消息
    db.add.assert_called_once()
    added = db.add.call_args.args[0]
    assert isinstance(added, ChatMessage)
    assert added.role == "user"
    assert added.content == "hi"
    assert added.session_id == sid

    # 跑了一条 UPDATE session 语句（维护 message_count + updated_at）
    assert db.execute.await_count == 1
    update_stmt = db.execute.await_args.args[0]
    # SQLAlchemy Update 语句的可读形态：包含 chat_sessions 表名 + 两个目标字段
    rendered = str(update_stmt)
    assert "chat_sessions" in rendered.lower()
    assert "message_count" in rendered.lower()
    assert "updated_at" in rendered.lower()

    # 事务提交 + refresh
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(msg)


async def test_append_message_with_tool_calls():
    """assistant 带 tool_calls 时也能正确写入。"""
    db = MagicMock()
    db.add = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    sid = uuid.uuid4()
    tc = [{"id": "1", "name": "search", "args": {"q": "x"}}]
    msg = await chat_service._append_message(
        db, sid, role="assistant", content=None, tool_calls=tc
    )
    assert msg.tool_calls == tc
    assert msg.content is None
