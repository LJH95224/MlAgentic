"""V1.5 KB-01~05 endpoint 行为测试（mock service 层，CI 友好）。

验证：
- 5 个 endpoint 的请求/响应包装、Pydantic 校验、错误码
- 不依赖真 Milvus / 真 PG

service 层真实业务逻辑（Milvus + PG 协调 / KB-05 严格清理顺序 / name 冲突回滚）
走 [tests/test_kb_v1_5_integration.py] 集成测试（真服务）。
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
from app.models.knowledge_base import (
    KB_STATUS_ACTIVE,
    KnowledgeBase,
)


def _make_kb(
    *,
    kb_id=None,
    name="法律法规库",
    description="存储所有合规相关文档",
    embedding_dim=4096,
    chunk_size=512,
    chunk_overlap=64,
    status=KB_STATUS_ACTIVE,
    file_count=0,
    chunk_count=0,
) -> KnowledgeBase:
    kb = KnowledgeBase(
        id=kb_id or uuid.uuid4(),
        name=name,
        description=description,
        embedding_dim=embedding_dim,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        status=status,
        file_count=file_count,
        chunk_count=chunk_count,
    )
    kb.created_at = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    return kb


@pytest.fixture
def client(monkeypatch):
    """TestClient + 空 DB 依赖（service 全部 mock）。"""

    async def _empty_db():
        yield None

    app.dependency_overrides[get_db] = _empty_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


# ──────────────── KB-01 创建 ────────────────


def test_create_kb_default_fields(client, monkeypatch):
    """不传 description / chunk_size / overlap → 用默认值。"""
    from app.services import kb_service

    fake = _make_kb()
    create_mock = AsyncMock(return_value=fake)
    monkeypatch.setattr(kb_service, "create_kb", create_mock)

    resp = client.post("/api/v1/knowledge-bases", json={"name": "法律法规库"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["code"] == error_codes.SUCCESS
    data = body["data"]
    assert data["name"] == "法律法规库"
    assert data["embedding_dim"] == 4096
    assert data["chunk_size"] == 512
    assert data["chunk_overlap"] == 64
    assert data["status"] == KB_STATUS_ACTIVE
    assert data["file_count"] == 0
    assert data["chunk_count"] == 0
    assert data["entity_count"] == 0  # 刚建的，0
    # service 应被以 PRD 默认值调用
    assert create_mock.await_args.kwargs == {
        "name": "法律法规库",
        "description": None,
        "embedding_dim": 4096,
        "chunk_size": 512,
        "chunk_overlap": 64,
    }


def test_create_kb_explicit_fields(client, monkeypatch):
    from app.services import kb_service

    fake = _make_kb(name="台风历史", description="台风路径文档", chunk_size=1024, chunk_overlap=128)
    create_mock = AsyncMock(return_value=fake)
    monkeypatch.setattr(kb_service, "create_kb", create_mock)

    resp = client.post(
        "/api/v1/knowledge-bases",
        json={
            "name": "台风历史",
            "description": "台风路径文档",
            "embedding_dim": 4096,
            "chunk_size": 1024,
            "chunk_overlap": 128,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["name"] == "台风历史"
    assert create_mock.await_args.kwargs["chunk_size"] == 1024
    assert create_mock.await_args.kwargs["chunk_overlap"] == 128


def test_create_kb_missing_name_rejected(client):
    resp = client.post("/api/v1/knowledge-bases", json={})
    assert resp.status_code == 422
    assert resp.json()["code"] == error_codes.PARAM_INVALID


def test_create_kb_blank_name_rejected(client):
    resp = client.post("/api/v1/knowledge-bases", json={"name": "   "})
    assert resp.status_code == 422
    assert resp.json()["code"] == error_codes.PARAM_INVALID


def test_create_kb_overlong_name_rejected(client):
    resp = client.post("/api/v1/knowledge-bases", json={"name": "x" * 129})
    assert resp.status_code == 422


def test_create_kb_overlong_description_rejected(client):
    resp = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "x", "description": "x" * 501},
    )
    assert resp.status_code == 422


def test_create_kb_chunk_size_out_of_range(client):
    """chunk_size 范围 128~2048（PRD KB-01）。"""
    too_small = client.post(
        "/api/v1/knowledge-bases", json={"name": "k", "chunk_size": 100}
    )
    too_big = client.post(
        "/api/v1/knowledge-bases", json={"name": "k", "chunk_size": 4096}
    )
    assert too_small.status_code == 422
    assert too_big.status_code == 422


def test_create_kb_overlap_exceeds_half_of_size_rejected(client):
    """chunk_overlap 不能超 chunk_size 的 50%（PRD KB-01）。"""
    resp = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "k", "chunk_size": 200, "chunk_overlap": 150},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == error_codes.PARAM_INVALID
    assert "chunk_overlap" in body["message"].lower()


def test_create_kb_overlap_at_half_allowed(client, monkeypatch):
    """chunk_overlap == chunk_size/2 是允许的（边界值）。"""
    from app.services import kb_service

    fake = _make_kb(chunk_size=200, chunk_overlap=100)
    monkeypatch.setattr(kb_service, "create_kb", AsyncMock(return_value=fake))

    resp = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "k", "chunk_size": 200, "chunk_overlap": 100},
    )
    assert resp.status_code == 201


def test_create_kb_name_conflict(client, monkeypatch):
    from app.services import kb_service

    monkeypatch.setattr(
        kb_service,
        "create_kb",
        AsyncMock(
            side_effect=BusinessError(
                error_codes.NAME_CONFLICT, "知识库名称 '气象库' 已存在"
            )
        ),
    )

    resp = client.post("/api/v1/knowledge-bases", json={"name": "气象库"})
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == error_codes.NAME_CONFLICT
    assert "气象库" in body["message"]


def test_create_kb_milvus_failure_returns_500(client, monkeypatch):
    """create_kb_collection 失败 service 抛 INTERNAL_ERROR → HTTP 500。"""
    from app.services import kb_service

    monkeypatch.setattr(
        kb_service,
        "create_kb",
        AsyncMock(
            side_effect=BusinessError(
                error_codes.INTERNAL_ERROR, "创建知识库底层资源失败：milvus down"
            )
        ),
    )

    resp = client.post("/api/v1/knowledge-bases", json={"name": "x"})
    assert resp.status_code == 500
    assert resp.json()["code"] == error_codes.INTERNAL_ERROR


# ──────────────── KB-02 列表 ────────────────


def test_list_kbs_default(client, monkeypatch):
    from app.services import kb_service

    items = [
        _make_kb(name="a", file_count=3, chunk_count=10),
        _make_kb(name="b"),
    ]
    monkeypatch.setattr(
        kb_service, "list_kbs", AsyncMock(return_value=(items, 5))
    )

    resp = client.get("/api/v1/knowledge-bases")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["total"] == 5
    assert len(data["items"]) == 2
    # 列表项不含 embedding_dim / chunk_size / entity_count
    first = data["items"][0]
    assert first["name"] == "a"
    assert first["file_count"] == 3
    assert first["chunk_count"] == 10
    assert "embedding_dim" not in first
    assert "entity_count" not in first


def test_list_kbs_explicit_pagination(client, monkeypatch):
    from app.services import kb_service

    list_mock = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(kb_service, "list_kbs", list_mock)

    resp = client.get("/api/v1/knowledge-bases?page=3&page_size=5")
    assert resp.status_code == 200
    assert list_mock.await_args.kwargs == {"page": 3, "page_size": 5}


def test_list_kbs_page_size_too_large(client):
    resp = client.get("/api/v1/knowledge-bases?page_size=200")
    assert resp.status_code == 422


# ──────────────── KB-03 详情 ────────────────


def test_get_kb_detail_ok(client, monkeypatch):
    from app.services import kb_service

    kid = uuid.uuid4()
    fake = _make_kb(kb_id=kid, name="d", file_count=2, chunk_count=8)
    monkeypatch.setattr(
        kb_service, "get_kb_or_raise", AsyncMock(return_value=fake)
    )
    monkeypatch.setattr(
        kb_service, "count_entities_for_kb", AsyncMock(return_value=42)
    )

    resp = client.get(f"/api/v1/knowledge-bases/{kid}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == str(kid)
    assert data["name"] == "d"
    assert data["file_count"] == 2
    assert data["chunk_count"] == 8
    # entity_count 走懒计算 hook，S2 stub 也能由 mock 控制
    assert data["entity_count"] == 42


def test_get_kb_detail_not_found(client, monkeypatch):
    from app.services import kb_service

    kid = uuid.uuid4()
    monkeypatch.setattr(
        kb_service,
        "get_kb_or_raise",
        AsyncMock(side_effect=BusinessError(error_codes.NOT_FOUND, f"知识库 {kid} 不存在")),
    )

    resp = client.get(f"/api/v1/knowledge-bases/{kid}")
    assert resp.status_code == 404
    assert resp.json()["code"] == error_codes.NOT_FOUND


def test_get_kb_detail_bad_uuid(client):
    resp = client.get("/api/v1/knowledge-bases/not-a-uuid")
    assert resp.status_code == 422


# ──────────────── KB-04 更新 ────────────────


def test_update_kb_name_only(client, monkeypatch):
    from app.services import kb_service

    kid = uuid.uuid4()
    fake = _make_kb(kb_id=kid, name="新名")
    update_mock = AsyncMock(return_value=fake)
    monkeypatch.setattr(kb_service, "update_kb", update_mock)

    resp = client.patch(f"/api/v1/knowledge-bases/{kid}", json={"name": "新名"})
    assert resp.status_code == 200
    assert resp.json()["data"]["name"] == "新名"
    # description 未传 → description_was_set=False
    kwargs = update_mock.await_args.kwargs
    assert kwargs["name"] == "新名"
    assert kwargs["description_was_set"] is False


def test_update_kb_description_to_null(client, monkeypatch):
    """显式传 description=null → 清空描述（description_was_set=True）。"""
    from app.services import kb_service

    kid = uuid.uuid4()
    fake = _make_kb(kb_id=kid, description=None)
    update_mock = AsyncMock(return_value=fake)
    monkeypatch.setattr(kb_service, "update_kb", update_mock)

    resp = client.patch(
        f"/api/v1/knowledge-bases/{kid}", json={"description": None}
    )
    assert resp.status_code == 200
    kwargs = update_mock.await_args.kwargs
    assert kwargs["description"] is None
    assert kwargs["description_was_set"] is True


def test_update_kb_both_fields(client, monkeypatch):
    from app.services import kb_service

    kid = uuid.uuid4()
    fake = _make_kb(kb_id=kid, name="N", description="D")
    monkeypatch.setattr(
        kb_service, "update_kb", AsyncMock(return_value=fake)
    )

    resp = client.patch(
        f"/api/v1/knowledge-bases/{kid}",
        json={"name": "N", "description": "D"},
    )
    assert resp.status_code == 200


def test_update_kb_empty_body_rejected(client):
    """name / description 都不传 → 业务规则要求至少一个。"""
    resp = client.patch(f"/api/v1/knowledge-bases/{uuid.uuid4()}", json={})
    assert resp.status_code == 422
    assert resp.json()["code"] == error_codes.PARAM_INVALID


def test_update_kb_extra_fields_rejected(client):
    """PRD 明确：embedding_dim / chunk_size / chunk_overlap 创建后只读。"""
    for forbidden_field in ("embedding_dim", "chunk_size", "chunk_overlap"):
        resp = client.patch(
            f"/api/v1/knowledge-bases/{uuid.uuid4()}",
            json={"name": "x", forbidden_field: 100},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == error_codes.PARAM_INVALID
        assert forbidden_field in body["message"].lower() or "extra" in body["message"].lower()


def test_update_kb_blank_name_rejected(client):
    resp = client.patch(
        f"/api/v1/knowledge-bases/{uuid.uuid4()}", json={"name": "   "}
    )
    assert resp.status_code == 422


def test_update_kb_overlong_name_rejected(client):
    resp = client.patch(
        f"/api/v1/knowledge-bases/{uuid.uuid4()}",
        json={"name": "x" * 129},
    )
    assert resp.status_code == 422


def test_update_kb_not_found(client, monkeypatch):
    from app.services import kb_service

    kid = uuid.uuid4()
    monkeypatch.setattr(
        kb_service,
        "update_kb",
        AsyncMock(side_effect=BusinessError(error_codes.NOT_FOUND, f"知识库 {kid} 不存在")),
    )

    resp = client.patch(f"/api/v1/knowledge-bases/{kid}", json={"name": "x"})
    assert resp.status_code == 404


def test_update_kb_name_conflict(client, monkeypatch):
    from app.services import kb_service

    kid = uuid.uuid4()
    monkeypatch.setattr(
        kb_service,
        "update_kb",
        AsyncMock(
            side_effect=BusinessError(
                error_codes.NAME_CONFLICT, "知识库名称 '冲突' 已被其它知识库占用"
            )
        ),
    )

    resp = client.patch(
        f"/api/v1/knowledge-bases/{kid}", json={"name": "冲突"}
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == error_codes.NAME_CONFLICT


# ──────────────── KB-05 删除 ────────────────


def test_delete_kb_ok(client, monkeypatch):
    from app.services import kb_service

    kid = uuid.uuid4()
    delete_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(kb_service, "delete_kb", delete_mock)

    resp = client.delete(f"/api/v1/knowledge-bases/{kid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == error_codes.SUCCESS
    assert body["data"] is None
    delete_mock.assert_awaited_once()


def test_delete_kb_not_found(client, monkeypatch):
    from app.services import kb_service

    kid = uuid.uuid4()
    monkeypatch.setattr(
        kb_service,
        "delete_kb",
        AsyncMock(side_effect=BusinessError(error_codes.NOT_FOUND, f"知识库 {kid} 不存在")),
    )

    resp = client.delete(f"/api/v1/knowledge-bases/{kid}")
    assert resp.status_code == 404


def test_delete_kb_milvus_failure_returns_500(client, monkeypatch):
    """KB-05 Milvus drop 失败 → service 抛 INTERNAL_ERROR → 500（整体回滚）。"""
    from app.services import kb_service

    kid = uuid.uuid4()
    monkeypatch.setattr(
        kb_service,
        "delete_kb",
        AsyncMock(
            side_effect=BusinessError(
                error_codes.INTERNAL_ERROR,
                "删除知识库底层向量资源失败：milvus connection refused",
            )
        ),
    )

    resp = client.delete(f"/api/v1/knowledge-bases/{kid}")
    assert resp.status_code == 500
    assert resp.json()["code"] == error_codes.INTERNAL_ERROR
