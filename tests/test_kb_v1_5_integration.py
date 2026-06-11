"""V1.5 KB-01~05 集成测试（依赖真 PostgreSQL + 真 Milvus；跳过 Neo4j）。

需要：
- TEST_DATABASE_URL=postgresql+asyncpg://...
- Milvus 实例运行中（按 .env MILVUS_URI 配置）

跑法：
    pytest tests/test_kb_v1_5_integration.py -v

每个用例都会真的在 Milvus 上创建/删除 Collection；用例结束 conftest.kb_client
fixture 会清理本测产生的 KB Collection，避免污染实例。
"""

import uuid

import pytest

from app.api import error_codes
from app.rag.naming import build_kb_collection_name
from tests.conftest import skip_without_db

pytestmark = [skip_without_db, pytest.mark.asyncio]


# 别名 fixture：让本文件用 `client` 自动注入 kb_client（含真 Milvus，无 Neo4j）
@pytest.fixture
def client(kb_client):
    return kb_client


def _milvus_has(name: str) -> bool:
    """直接查 Milvus 看 collection 是否存在；用于跨断言验证副作用。"""
    from app.rag.milvus_client import get_milvus_client

    return get_milvus_client().has_collection(name)


# ──────────────── KB-01 创建 ────────────────


async def test_kb_create_writes_pg_and_milvus(client):
    """创建成功 → PG 有记录、Milvus 有对应 collection。"""
    resp = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "气象库-集成测试", "description": "联调用"},
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    kb_id = uuid.UUID(data["id"])

    # PG 端：详情接口能查到
    detail = await client.get(f"/api/v1/knowledge-bases/{kb_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["name"] == "气象库-集成测试"

    # Milvus 端：collection 存在
    assert _milvus_has(build_kb_collection_name(kb_id))


async def test_kb_create_default_dim_and_chunk_params(client):
    """不传 dim/chunk_size/overlap 用 PRD 默认值。"""
    resp = await client.post(
        "/api/v1/knowledge-bases", json={"name": "默认参数测试"}
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["embedding_dim"] == 4096
    assert data["chunk_size"] == 512
    assert data["chunk_overlap"] == 64


async def test_kb_create_name_conflict_returns_409(client):
    r1 = await client.post(
        "/api/v1/knowledge-bases", json={"name": "冲突测试"}
    )
    assert r1.status_code == 201

    r2 = await client.post(
        "/api/v1/knowledge-bases", json={"name": "冲突测试"}
    )
    assert r2.status_code == 409
    assert r2.json()["code"] == error_codes.NAME_CONFLICT


async def test_kb_create_invalid_chunk_overlap_400(client):
    resp = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "参数错", "chunk_size": 200, "chunk_overlap": 150},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == error_codes.PARAM_INVALID


# ──────────────── KB-02 列表 ────────────────


async def test_kb_list_pagination_and_total(client):
    """连续建 3 个 → list 总数 ≥3，page_size=2 应分 2 页。"""
    for i in range(3):
        await client.post(
            "/api/v1/knowledge-bases", json={"name": f"列表测试-{i}"}
        )

    p1 = await client.get("/api/v1/knowledge-bases?page=1&page_size=2")
    assert p1.status_code == 200
    d1 = p1.json()["data"]
    assert d1["total"] >= 3
    assert len(d1["items"]) == 2
    assert d1["page"] == 1
    assert d1["page_size"] == 2

    p2 = await client.get("/api/v1/knowledge-bases?page=2&page_size=2")
    d2 = p2.json()["data"]
    # 各页 id 不重复
    ids = [i["id"] for i in d1["items"]] + [i["id"] for i in d2["items"]]
    assert len(set(ids)) == len(ids)


async def test_kb_list_order_by_created_at_desc(client):
    """最后建的 KB 应排在列表第一位。"""
    names = ["顺序测试-1", "顺序测试-2", "顺序测试-3"]
    for n in names:
        await client.post("/api/v1/knowledge-bases", json={"name": n})

    resp = await client.get("/api/v1/knowledge-bases?page=1&page_size=10")
    items = resp.json()["data"]["items"]
    # 后建的 -3 在前
    found = [i["name"] for i in items if i["name"] in names]
    assert found[:3] == ["顺序测试-3", "顺序测试-2", "顺序测试-1"]


# ──────────────── KB-03 详情 ────────────────


async def test_kb_detail_includes_all_fields(client):
    r = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "详情测试", "description": "desc"},
    )
    kb_id = r.json()["data"]["id"]

    detail = await client.get(f"/api/v1/knowledge-bases/{kb_id}")
    assert detail.status_code == 200
    data = detail.json()["data"]
    # 详情字段完整（PRD KB-03）
    for key in (
        "id",
        "name",
        "description",
        "embedding_dim",
        "chunk_size",
        "chunk_overlap",
        "status",
        "file_count",
        "chunk_count",
        "entity_count",
        "created_at",
    ):
        assert key in data, f"detail 缺字段 {key}"
    # S2 阶段 entity_count 走 stub 返回 0
    assert data["entity_count"] == 0


async def test_kb_detail_not_found(client):
    bogus = "00000000-0000-0000-0000-000000000000"
    resp = await client.get(f"/api/v1/knowledge-bases/{bogus}")
    assert resp.status_code == 404


# ──────────────── KB-04 更新 ────────────────


async def test_kb_update_name_persisted(client):
    r = await client.post(
        "/api/v1/knowledge-bases", json={"name": "原名-更新测试"}
    )
    kb_id = r.json()["data"]["id"]

    patched = await client.patch(
        f"/api/v1/knowledge-bases/{kb_id}", json={"name": "新名-更新测试"}
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["name"] == "新名-更新测试"

    # 再查详情确认持久化
    detail = await client.get(f"/api/v1/knowledge-bases/{kb_id}")
    assert detail.json()["data"]["name"] == "新名-更新测试"


async def test_kb_update_description_clear(client):
    r = await client.post(
        "/api/v1/knowledge-bases",
        json={"name": "清空描述测试", "description": "原描述"},
    )
    kb_id = r.json()["data"]["id"]

    # 显式传 description=null 清空
    patched = await client.patch(
        f"/api/v1/knowledge-bases/{kb_id}", json={"description": None}
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["description"] is None


async def test_kb_update_name_conflict(client):
    r1 = await client.post("/api/v1/knowledge-bases", json={"name": "A-冲突"})
    r2 = await client.post("/api/v1/knowledge-bases", json={"name": "B-冲突"})
    a_id = r1.json()["data"]["id"]

    # 把 A 改成 B 的名字 → 冲突
    resp = await client.patch(
        f"/api/v1/knowledge-bases/{a_id}", json={"name": "B-冲突"}
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == error_codes.NAME_CONFLICT


async def test_kb_update_immutable_field_blocked_at_schema(client):
    """PRD 明确：embedding_dim 创建后只读，传入应被 422 拦截。"""
    r = await client.post(
        "/api/v1/knowledge-bases", json={"name": "只读字段测试"}
    )
    kb_id = r.json()["data"]["id"]

    resp = await client.patch(
        f"/api/v1/knowledge-bases/{kb_id}",
        json={"name": "x", "embedding_dim": 1024},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == error_codes.PARAM_INVALID


# ──────────────── KB-05 删除 ────────────────


async def test_kb_delete_cleans_pg_and_milvus(client):
    """删除 → PG 记录消失 + Milvus collection 真 drop。"""
    r = await client.post(
        "/api/v1/knowledge-bases", json={"name": "删除测试"}
    )
    kb_id = uuid.UUID(r.json()["data"]["id"])
    collection_name = build_kb_collection_name(kb_id)

    # 创建后两端都有
    assert _milvus_has(collection_name)
    assert (
        await client.get(f"/api/v1/knowledge-bases/{kb_id}")
    ).status_code == 200

    # 删除
    deleted = await client.delete(f"/api/v1/knowledge-bases/{kb_id}")
    assert deleted.status_code == 200
    assert deleted.json()["code"] == error_codes.SUCCESS

    # PG 没了
    assert (
        await client.get(f"/api/v1/knowledge-bases/{kb_id}")
    ).status_code == 404
    # Milvus 也没了
    assert not _milvus_has(collection_name)


async def test_kb_delete_not_found(client):
    bogus = "00000000-0000-0000-0000-000000000000"
    resp = await client.delete(f"/api/v1/knowledge-bases/{bogus}")
    assert resp.status_code == 404
