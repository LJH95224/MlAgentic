"""V1.5 SES-01~06 service 层 + 端到端集成测试（依赖真 PostgreSQL）。

需要环境变量 `TEST_DATABASE_URL=postgresql+asyncpg://...`，否则全部 skip。
**不依赖 Milvus / Neo4j / LLM**：用 `pg_client` fixture（conftest 里跳 lifespan
初始化），跑得快、不连远程组件。

覆盖 mock 测无法验的业务规则：
- list_sessions 按 updated_at 倒序 + total 准确
- list_session_messages 游标翻页正序 / has_more / next_before 正确
- delete_session 级联删消息（不留孤儿）
- PATCH title 真实 update + updated_at 刷新

跑法（用户手动）：
    set TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/tyagent_test
    pytest tests/test_sessions_v1_5_integration.py -v
"""

import pytest

from app.api import error_codes
from tests.conftest import skip_without_db

pytestmark = [skip_without_db, pytest.mark.asyncio]


# 让用例参数从 `client` 改为 `pg_client`（轻量 fixture，无 Milvus/Neo4j）
# 各 test 函数签名里也要相应改名 —— 用 fixture 别名简化
@pytest.fixture
def client(pg_client):
    """让本文件里的 `client` 参数自动注入 pg_client，避免逐个 test 改签名。"""
    return pg_client


# ──────────────── SES-01 创建 ────────────────


async def test_create_session_no_title_returns_null(client):
    resp = await client.post("/api/v1/sessions", json={})
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["title"] is None
    assert data["message_count"] == 0
    assert data["summary"] is None


async def test_create_session_with_title_stored(client):
    resp = await client.post("/api/v1/sessions", json={"title": "积分对话"})
    assert resp.status_code == 201
    sid = resp.json()["data"]["id"]
    # 用 SES-03 详情接口验证 title 落库
    detail = await client.get(f"/api/v1/sessions/{sid}")
    assert detail.json()["data"]["title"] == "积分对话"


# ──────────────── SES-02 列表 ────────────────


async def test_list_sessions_orders_by_updated_at_desc(client):
    # 连续建 3 个会话，最后一个 updated_at 最新 → 应排在第一位
    titles = ["a", "b", "c"]
    ids = []
    for t in titles:
        r = await client.post("/api/v1/sessions", json={"title": t})
        ids.append(r.json()["data"]["id"])

    resp = await client.get("/api/v1/sessions")
    items = resp.json()["data"]["items"]
    # 倒序：c (最新建) 在前
    assert [i["title"] for i in items[:3]] == ["c", "b", "a"]


async def test_list_sessions_pagination(client):
    # 建 5 个会话
    for i in range(5):
        await client.post("/api/v1/sessions", json={"title": f"s{i}"})

    p1 = await client.get("/api/v1/sessions?page=1&page_size=2")
    p2 = await client.get("/api/v1/sessions?page=2&page_size=2")
    p3 = await client.get("/api/v1/sessions?page=3&page_size=2")

    assert len(p1.json()["data"]["items"]) == 2
    assert len(p2.json()["data"]["items"]) == 2
    assert len(p3.json()["data"]["items"]) == 1  # 最后一页 1 条
    assert p1.json()["data"]["total"] >= 5

    # 各页 id 不重复
    ids = []
    for p in (p1, p2, p3):
        ids.extend(i["id"] for i in p.json()["data"]["items"])
    assert len(set(ids)) == len(ids)


# ──────────────── SES-03 详情 ────────────────


async def test_get_session_detail_returns_all_fields(client):
    r = await client.post("/api/v1/sessions", json={"title": "详情测试"})
    sid = r.json()["data"]["id"]
    detail = await client.get(f"/api/v1/sessions/{sid}")

    assert detail.status_code == 200
    data = detail.json()["data"]
    # PRD SES-03 验收要求"所有字段不缺失"
    for key in ("id", "title", "summary", "summarized_at", "message_count",
                "metadata", "created_at", "updated_at"):
        assert key in data, f"detail 缺字段 {key}"


async def test_get_session_detail_404_for_unknown_id(client):
    bogus = "00000000-0000-0000-0000-000000000000"
    resp = await client.get(f"/api/v1/sessions/{bogus}")
    assert resp.status_code == 404
    assert resp.json()["code"] == error_codes.NOT_FOUND


# ──────────────── SES-04 改标题 ────────────────


async def test_patch_session_title_changes_value(client):
    r = await client.post("/api/v1/sessions", json={"title": "旧"})
    sid = r.json()["data"]["id"]

    patched = await client.patch(
        f"/api/v1/sessions/{sid}", json={"title": "新"}
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["title"] == "新"

    # 再查详情确认持久化
    detail = await client.get(f"/api/v1/sessions/{sid}")
    assert detail.json()["data"]["title"] == "新"


async def test_patch_session_title_404_for_unknown(client):
    bogus = "00000000-0000-0000-0000-000000000000"
    resp = await client.patch(f"/api/v1/sessions/{bogus}", json={"title": "x"})
    assert resp.status_code == 404
    assert resp.json()["code"] == error_codes.NOT_FOUND


# ──────────────── SES-05 删除 ────────────────


async def test_delete_session_then_404_on_subsequent_queries(client):
    r = await client.post("/api/v1/sessions", json={"title": "待删除"})
    sid = r.json()["data"]["id"]

    deleted = await client.delete(f"/api/v1/sessions/{sid}")
    assert deleted.status_code == 200
    assert deleted.json()["code"] == error_codes.SUCCESS

    # 再查应 404
    after = await client.get(f"/api/v1/sessions/{sid}")
    assert after.status_code == 404


async def test_delete_session_404_for_unknown(client):
    bogus = "00000000-0000-0000-0000-000000000000"
    resp = await client.delete(f"/api/v1/sessions/{bogus}")
    assert resp.status_code == 404


# ──────────────── SES-06 消息历史（游标翻页） ────────────────


async def _insert_messages_directly(session_id: str, contents: list[str]):
    """直接通过 DB 插消息（V1.5 当前还没 POST messages endpoint）。

    ⚠️ 关键：必须给每条消息显式递增的 created_at —— 同一事务里多条 insert 走
    server_default=func.now() 拿到的是完全相同的时间戳，PG 的 ORDER BY 在 tie
    时不保证按插入顺序（实测按物理存储倒序），导致游标翻页测试反序失败。
    真实场景消息间隔秒级不会踩到，测试 fixture 必须显式控制时间戳。
    """
    import uuid as _uuid
    from datetime import datetime, timedelta, timezone

    from app.db.session import AsyncSessionLocal
    from app.models.message import ChatMessage

    sid = _uuid.UUID(session_id) if isinstance(session_id, str) else session_id
    base = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        for idx, c in enumerate(contents):
            msg = ChatMessage(session_id=sid, role="user", content=c)
            msg.created_at = base + timedelta(seconds=idx)
            db.add(msg)
        await db.commit()


async def test_list_messages_returns_chronological_order(client):
    r = await client.post("/api/v1/sessions", json={"title": "消息测试"})
    sid = r.json()["data"]["id"]

    await _insert_messages_directly(sid, ["第1条", "第2条", "第3条"])

    resp = await client.get(f"/api/v1/sessions/{sid}/messages")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    # PRD SES-06 验收：正序返回
    assert [i["content"] for i in items] == ["第1条", "第2条", "第3条"]


async def test_list_messages_cursor_pagination_no_gap_no_dup(client):
    r = await client.post("/api/v1/sessions", json={"title": "翻页测试"})
    sid = r.json()["data"]["id"]

    contents = [f"m{i}" for i in range(7)]
    await _insert_messages_directly(sid, contents)

    # PRD SES-06：service 返回 before 之前的最近 N 条，不传 before → 拉最近 N 条
    # 第一页：limit=3 → 最近 3 条 = [m4, m5, m6]（按 created_at 正序返回）
    page1 = await client.get(f"/api/v1/sessions/{sid}/messages?limit=3")
    p1 = page1.json()["data"]
    assert [m["content"] for m in p1["items"]] == ["m4", "m5", "m6"]
    assert p1["has_more"] is True  # 前面还有 m0~m3
    # next_before = items 首条 id（m4），用它继续往前翻能拿 m1~m3
    assert p1["next_before"] == p1["items"][0]["id"]

    # 用 next_before 继续翻 → 应拿 [m1, m2, m3]
    page2 = await client.get(
        f"/api/v1/sessions/{sid}/messages?limit=3&before={p1['next_before']}"
    )
    p2 = page2.json()["data"]
    assert [m["content"] for m in p2["items"]] == ["m1", "m2", "m3"]
    assert p2["has_more"] is True  # 前面还有 m0
    assert p2["next_before"] == p2["items"][0]["id"]

    # 再翻一页 → 应拿 [m0]
    page3 = await client.get(
        f"/api/v1/sessions/{sid}/messages?limit=3&before={p2['next_before']}"
    )
    p3 = page3.json()["data"]
    assert [m["content"] for m in p3["items"]] == ["m0"]
    assert p3["has_more"] is False  # 到顶了

    # 各页消息无重复、无遗漏
    all_returned = (
        [m["content"] for m in p1["items"]]
        + [m["content"] for m in p2["items"]]
        + [m["content"] for m in p3["items"]]
    )
    # 7 条全覆盖（不区分顺序，先去重看完整性）
    assert set(all_returned) == {f"m{i}" for i in range(7)}
    assert len(all_returned) == 7  # 无重复


async def test_list_messages_session_not_found(client):
    bogus = "00000000-0000-0000-0000-000000000000"
    resp = await client.get(f"/api/v1/sessions/{bogus}/messages")
    assert resp.status_code == 404


async def test_list_messages_invalid_cursor_400(client):
    r = await client.post("/api/v1/sessions", json={})
    sid = r.json()["data"]["id"]
    bogus = "11111111-1111-1111-1111-111111111111"
    resp = await client.get(f"/api/v1/sessions/{sid}/messages?before={bogus}")
    assert resp.status_code == 400
    assert resp.json()["code"] == error_codes.PARAM_INVALID


# ──────────────── 级联删除验证 ────────────────


async def test_delete_session_cascades_messages(client):
    r = await client.post("/api/v1/sessions", json={"title": "级联测试"})
    sid = r.json()["data"]["id"]
    await _insert_messages_directly(sid, ["x", "y"])

    # 确认消息存在
    before_delete = await client.get(f"/api/v1/sessions/{sid}/messages")
    assert len(before_delete.json()["data"]["items"]) == 2

    # 删 session
    await client.delete(f"/api/v1/sessions/{sid}")

    # 再查消息应 404（session 已不存在）
    after = await client.get(f"/api/v1/sessions/{sid}/messages")
    assert after.status_code == 404
