"""V1.5 SES-09 + chat_service 集成测试（依赖真 PostgreSQL）。

不依赖真 LLM / Milvus / Neo4j（用 conftest 的 `pg_client` 跳过相关初始化），
只覆盖：
- _load_history 真在 PG 上按 SES-09 窗口截断
- _append_message 真把 message_count / updated_at 写到行上

跑法（用户手动）：
    set TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/tyagent_test
    pytest tests/test_chat_service_v1_5_integration.py -v
"""

import asyncio
import uuid
from contextlib import asynccontextmanager

import pytest

from tests.conftest import skip_without_db

pytestmark = [skip_without_db, pytest.mark.asyncio]


# 别名 fixture：让本文件 `client` 参数自动注入 pg_client（轻量，无 Milvus/Neo4j）
@pytest.fixture
def client(pg_client):
    return pg_client


# ──────────────── DB helpers ────────────────


@asynccontextmanager
async def _open_db():
    """打开一个独立 AsyncSession，用 async with 自动关闭。

    旧写法 `async for db in get_db_session(): ... break` 在 break 时会让 generator
    清理路径不确定，集成测试里偶发卡顿。这里直接用 AsyncSessionLocal 拿干净 session。
    """
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session


async def _new_session_id(client) -> uuid.UUID:
    """通过 API 建一个会话，返回 UUID。"""
    r = await client.post("/api/v1/sessions", json={"title": "ctx-test"})
    return uuid.UUID(r.json()["data"]["id"])


async def _bulk_insert_messages(
    session_id: uuid.UUID, items: list[tuple[str, str | None]]
) -> None:
    """直接通过 ORM 插消息；items = [(role, content), ...]。

    session_id 必须是 UUID 对象，asyncpg 的 UUID 列不接受字符串。

    ⚠️ 关键：必须给每条消息显式递增的 created_at —— 同一事务里多条 insert 走
    server_default=func.now() 拿到的是**完全相同的时间戳**，PG 的 ORDER BY
    created_at 在 tie 时不保证按插入顺序，会按物理存储顺序（实测倒序）返回，
    导致 _load_history / list_messages 测试反序失败。真实场景里消息间隔秒级
    不会踩到，但测试 fixture 必须自己控制时间戳。
    """
    from datetime import datetime, timedelta, timezone

    from app.models.message import ChatMessage

    base = datetime.now(timezone.utc)
    async with _open_db() as db:
        for idx, (role, content) in enumerate(items):
            msg = ChatMessage(session_id=session_id, role=role, content=content)
            # 每条 +1 秒，绝对错开 tie
            msg.created_at = base + timedelta(seconds=idx)
            db.add(msg)
        await db.commit()


# ──────────────── _load_history × 真 PG ────────────────


async def test_load_history_real_db_within_window(monkeypatch, client):
    """窗口够大 → 历史全部返回；system 在前、非 system 正序。"""
    monkeypatch.setenv("CONTEXT_WINDOW_MESSAGES", "20")
    from app.core.config import get_settings

    get_settings.cache_clear()

    sid = await _new_session_id(client)
    await _bulk_insert_messages(
        sid,
        [
            ("system", "sys-1"),
            ("user", "u1"),
            ("assistant", "a1"),
            ("user", "u2"),
        ],
    )

    from app.services import chat_service

    async with _open_db() as db:
        history = await chat_service._load_history(db, sid)

    assert [h["role"] for h in history] == ["system", "user", "assistant", "user"]
    assert [h["content"] for h in history] == ["sys-1", "u1", "a1", "u2"]


async def test_load_history_real_db_truncates_over_window(monkeypatch, client):
    """窗口=3，10 条非 system → 只剩最近 3 条；system 全保留。"""
    monkeypatch.setenv("CONTEXT_WINDOW_MESSAGES", "3")
    from app.core.config import get_settings

    get_settings.cache_clear()

    sid = await _new_session_id(client)
    items: list[tuple[str, str | None]] = [("system", "S")]
    for i in range(10):
        items.append(("user" if i % 2 == 0 else "assistant", f"m{i}"))
    await _bulk_insert_messages(sid, items)

    from app.services import chat_service

    async with _open_db() as db:
        history = await chat_service._load_history(db, sid)

    # system + 最近 3 条非 system
    assert [h["content"] for h in history] == ["S", "m7", "m8", "m9"]


# ──────────────── _append_message × 真 PG ────────────────


async def test_append_message_increments_count_and_touches_updated_at(client):
    """连续写 3 条消息 → message_count == 3 且 updated_at 单调推进。"""
    from app.services import chat_service, session_service

    sid = await _new_session_id(client)

    # 写第一条
    async with _open_db() as db:
        await chat_service._append_message(db, sid, role="user", content="一")
    # 取 updated_at 1
    async with _open_db() as db:
        s1 = await session_service.get_session_or_raise(db, sid)
        c1, t1 = s1.message_count, s1.updated_at
    assert c1 == 1

    # 等一小会儿让时间戳不冲突（PG 时间戳通常微秒，给点 buffer）
    await asyncio.sleep(0.05)

    # 再写两条
    async with _open_db() as db:
        await chat_service._append_message(db, sid, role="assistant", content="二")
        await chat_service._append_message(db, sid, role="user", content="三")

    async with _open_db() as db:
        s2 = await session_service.get_session_or_raise(db, sid)
        c2, t2 = s2.message_count, s2.updated_at

    assert c2 == 3
    assert t2 >= t1  # updated_at 单调不回退


async def test_append_message_then_session_list_orders_by_updated_at(client):
    """在 A 会话后再 B 会话；给 A 写一条消息 → A 应排到列表第一位（updated_at 倒序）。"""
    from app.services import chat_service

    a_id = await _new_session_id(client)
    b_id = await _new_session_id(client)

    # 给 A 追加消息 → A.updated_at 跳到最新
    async with _open_db() as db:
        await chat_service._append_message(db, a_id, role="user", content="x")

    resp = await client.get("/api/v1/sessions?page=1&page_size=10")
    items = resp.json()["data"]["items"]
    # A 应该排在 B 前面
    a_idx = next(i for i, it in enumerate(items) if it["id"] == str(a_id))
    b_idx = next(i for i, it in enumerate(items) if it["id"] == str(b_id))
    assert a_idx < b_idx, "写消息后 A 的 updated_at 应让它排在 B 前面"

    # A 的 message_count 应为 1
    a_item = items[a_idx]
    assert a_item["message_count"] == 1


# ──────────────── 与 SES-06 联动 ────────────────


async def test_append_then_list_messages_includes_new_one(client):
    """新写的消息能通过 SES-06 历史接口看到。"""
    from app.services import chat_service

    sid = await _new_session_id(client)
    async with _open_db() as db:
        await chat_service._append_message(db, sid, role="user", content="hello-world")

    resp = await client.get(f"/api/v1/sessions/{sid}/messages")
    items = resp.json()["data"]["items"]
    assert any(m["content"] == "hello-world" for m in items)
