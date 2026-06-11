"""会话接口测试（V1.0 API-01；V1.5 已包统一响应格式）。

V1.5 改造（2026-06-11）：fixture 从重型 `client`（依赖 Milvus + Neo4j + LLM）
改为轻量 `pg_client`（仅 PG），因为这些用例本就只测 POST /sessions 和 /health，
跟 Milvus / Neo4j 无关。改完后 CI 不需要起 Neo4j 也能跑全量集成测试。

真正需要走 RAG + KG + LLM 的端到端测试（test_chat_stream）仍用 `client` fixture。
"""

import uuid

import pytest

from app.api import error_codes
from tests.conftest import skip_without_db


# 用别名 fixture 把本文件里 `client` 参数自动注入 pg_client（不连 Milvus/Neo4j）
@pytest.fixture
def client(pg_client):
    return pg_client


@skip_without_db
async def test_create_session(client):
    """POST /api/v1/sessions 应返回 201 + ApiResponse{code:0,data:{id,created_at}}。"""
    resp = await client.post("/api/v1/sessions")

    assert resp.status_code == 201
    body = resp.json()
    # V1.5 起统一响应格式（PRD §7.1）
    assert body["code"] == error_codes.SUCCESS
    assert body["message"] == "success"
    data = body["data"]
    assert "id" in data
    assert "created_at" in data

    # id 必须是合法 UUID
    parsed = uuid.UUID(data["id"])
    assert parsed.version is not None


@skip_without_db
async def test_create_session_returns_unique_ids(client):
    """连续创建两个会话应返回不同的 ID。"""
    r1 = await client.post("/api/v1/sessions")
    r2 = await client.post("/api/v1/sessions")

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["data"]["id"] != r2.json()["data"]["id"]


async def test_health(client):
    """健康检查不依赖 DB，应始终通过；V1.5 仍按原裸 JSON 返回（不属于 v1 业务接口）。"""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
