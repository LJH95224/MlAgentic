"""会话接口测试（API-01）。"""

import uuid

from tests.conftest import skip_without_db


@skip_without_db
async def test_create_session(client):
    """POST /api/v1/sessions 应该返回 201 与合法 UUID。"""
    resp = await client.post("/api/v1/sessions")

    assert resp.status_code == 201
    body = resp.json()
    assert "id" in body
    assert "created_at" in body

    # id 必须是合法 UUID
    parsed = uuid.UUID(body["id"])
    assert parsed.version is not None


@skip_without_db
async def test_create_session_returns_unique_ids(client):
    """连续创建两个会话应返回不同的 ID。"""
    r1 = await client.post("/api/v1/sessions")
    r2 = await client.post("/api/v1/sessions")

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]


async def test_health(client):
    """健康检查不依赖 DB，应始终通过。"""
    # 这里用了 client 夹具但接口本身不读 DB；如果没 DB 仍会被 skip，
    # 是因为 client 夹具本身依赖 DB。在 V1.0 阶段这是可接受的开销。
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}