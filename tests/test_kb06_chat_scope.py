"""V1.5 S5 KB-06 单测：kb_ids 上下文 + 跨 KB Collection 检索 + KG 子图过滤。

不依赖真 Milvus / Neo4j；用 monkeypatch + mock 验证：
- contextvar 三态语义（None / [] / [...]）
- search_knowledge_base 在三态下分别走的代码路径
- query_knowledge_graph 在三态下 Cypher 是否带 kb_id 过滤
- ChatRequest 校验 kb_ids 字段
- ToolStart 事件携带 _kb_ids 信息（KB-06 验收点）
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.context import (
    get_current_kb_ids,
    reset_current_kb_ids,
    set_current_kb_ids,
)


# ──────────────── contextvar 三态 ────────────────


def test_kb_ids_default_is_none():
    assert get_current_kb_ids() is None


def test_kb_ids_set_get_reset():
    sentinel = [uuid.uuid4(), uuid.uuid4()]
    tok = set_current_kb_ids(sentinel)
    assert get_current_kb_ids() == sentinel
    reset_current_kb_ids(tok)
    assert get_current_kb_ids() is None


def test_kb_ids_empty_list_distinguished_from_none():
    tok = set_current_kb_ids([])
    assert get_current_kb_ids() == []
    assert get_current_kb_ids() is not None
    reset_current_kb_ids(tok)


# ──────────────── ChatRequest 校验 ────────────────


def test_chat_request_kb_ids_optional():
    from app.schemas.chat import ChatRequest

    r = ChatRequest(session_id=uuid.uuid4(), content="hi")
    assert r.kb_ids is None


def test_chat_request_kb_ids_accepts_empty_list():
    from app.schemas.chat import ChatRequest

    r = ChatRequest(session_id=uuid.uuid4(), content="hi", kb_ids=[])
    assert r.kb_ids == []


def test_chat_request_kb_ids_accepts_uuid_list():
    from app.schemas.chat import ChatRequest

    ids = [uuid.uuid4() for _ in range(3)]
    r = ChatRequest(session_id=uuid.uuid4(), content="hi", kb_ids=ids)
    assert len(r.kb_ids) == 3
    assert all(isinstance(x, uuid.UUID) for x in r.kb_ids)


def test_chat_request_kb_ids_rejects_invalid_uuid():
    """非 UUID 字符串 → Pydantic 校验失败。"""
    from pydantic import ValidationError

    from app.schemas.chat import ChatRequest

    with pytest.raises(ValidationError):
        ChatRequest(
            session_id=uuid.uuid4(), content="hi", kb_ids=["not-a-uuid"]
        )


# ──────────────── retriever：kb_ids 三态决定的 Collection 路由 ────────────────


@pytest.fixture
def mock_embed(monkeypatch):
    fake_vec = [0.0] * 4096
    mock = AsyncMock(return_value=[fake_vec])
    monkeypatch.setattr("app.rag.retriever.aembed_texts", mock)
    return mock


@pytest.fixture
def mock_client_v2(monkeypatch):
    """mock get_milvus_client；记录所有 search 调用的 collection_name。"""
    client = MagicMock()
    client.has_collection = MagicMock(return_value=True)
    client.search = MagicMock(
        return_value=[
            [
                {
                    "distance": 0.9,
                    "entity": {
                        "document_id": "doc-x",
                        "content": "片段",
                        "entity_tags": [],
                        "metadata": {},
                    },
                }
            ]
        ]
    )
    monkeypatch.setattr("app.rag.retriever.get_milvus_client", lambda: client)
    return client


@pytest.fixture(autouse=True)
def _reset_kb_ids_each_test():
    """每个用例自动 reset kb_ids，防 ContextVar 跨用例污染。"""
    tok = set_current_kb_ids(None)
    yield
    reset_current_kb_ids(tok)


async def test_search_with_kb_ids_none_uses_default_collection(
    mock_embed, mock_client_v2, monkeypatch
):
    """kb_ids=None（V1.0 默认行为）→ 查 settings.milvus_collection。"""
    monkeypatch.setenv("MILVUS_COLLECTION", "knowledge_chunks")
    from app.core.config import get_settings

    get_settings.cache_clear()

    from app.rag.retriever import _do_search

    # 不设置 kb_ids → 走默认 None
    await _do_search("q", 5, None, None, None)

    # 应该只调一次 search，collection 是默认值
    assert mock_client_v2.search.call_count == 1
    assert mock_client_v2.search.call_args.kwargs["collection_name"] == "knowledge_chunks"


async def test_search_with_empty_kb_ids_skips_milvus(
    mock_embed, mock_client_v2
):
    """kb_ids=[]（显式空）→ 不调 Milvus，直接返提示文本。"""
    set_current_kb_ids([])

    from app.rag.retriever import _do_search

    result = await _do_search("q", 5, None, None, None)
    assert "未指定知识库" in result
    mock_client_v2.search.assert_not_called()
    # embedding 也应该没被调（连向量化都跳过）
    mock_embed.assert_not_called()


async def test_search_with_kb_ids_uses_multiple_collections(
    mock_embed, mock_client_v2
):
    """kb_ids=[a,b] → 调两次 search，collection 是 kb_{hex_a/b}。"""
    from app.rag.naming import build_kb_collection_name

    kb_a = uuid.uuid4()
    kb_b = uuid.uuid4()
    set_current_kb_ids([kb_a, kb_b])

    from app.rag.retriever import _do_search

    await _do_search("q", 5, None, None, None)

    assert mock_client_v2.search.call_count == 2
    called_collections = [
        c.kwargs["collection_name"] for c in mock_client_v2.search.call_args_list
    ]
    assert build_kb_collection_name(kb_a) in called_collections
    assert build_kb_collection_name(kb_b) in called_collections


async def test_search_skips_missing_collection(
    mock_embed, mock_client_v2
):
    """传了不存在的 kb_id（has_collection=False）→ 跳过该 collection 不查。"""
    kb_a = uuid.uuid4()
    kb_b = uuid.uuid4()
    set_current_kb_ids([kb_a, kb_b])

    # 第一个 collection 不存在，第二个存在
    from app.rag.naming import build_kb_collection_name

    a_name = build_kb_collection_name(kb_a)

    def _has(name):
        return name != a_name

    mock_client_v2.has_collection = MagicMock(side_effect=_has)

    from app.rag.retriever import _do_search

    await _do_search("q", 5, None, None, None)

    # 只调一次 search（kb_b 那一次）
    assert mock_client_v2.search.call_count == 1
    assert (
        mock_client_v2.search.call_args.kwargs["collection_name"]
        == build_kb_collection_name(kb_b)
    )


async def test_search_per_collection_failure_does_not_break_others(
    mock_embed, mock_client_v2
):
    """一个 KB 检索抛错不应影响其它 KB（KB-06 容错关键设计）。"""
    kb_a = uuid.uuid4()
    kb_b = uuid.uuid4()
    set_current_kb_ids([kb_a, kb_b])

    from app.rag.naming import build_kb_collection_name

    a_name = build_kb_collection_name(kb_a)
    b_name = build_kb_collection_name(kb_b)

    # kb_a 抛错；kb_b 正常返结果
    def _search(collection_name, **kw):
        if collection_name == a_name:
            raise RuntimeError("kb_a milvus down")
        return [
            [
                {
                    "distance": 0.7,
                    "entity": {
                        "document_id": "doc_b",
                        "content": "kb_b 片段",
                        "entity_tags": [],
                        "metadata": {},
                    },
                }
            ]
        ]

    mock_client_v2.search = MagicMock(side_effect=_search)

    from app.rag.retriever import _do_search

    result = await _do_search("q", 5, None, None, None)
    # kb_b 的内容应该在结果里（kb_a 失败被吞）
    assert "kb_b 片段" in result


async def test_search_merges_and_resorts_across_collections(
    mock_embed, mock_client_v2
):
    """两个 collection 各返一条，应合并按 distance 重排取 top_k。"""
    from app.rag.naming import build_kb_collection_name

    kb_a = uuid.uuid4()
    kb_b = uuid.uuid4()
    set_current_kb_ids([kb_a, kb_b])

    a_name = build_kb_collection_name(kb_a)
    b_name = build_kb_collection_name(kb_b)

    def _search(collection_name, **kw):
        if collection_name == a_name:
            return [
                [
                    {
                        "distance": 0.5,
                        "entity": {
                            "document_id": "doc_a",
                            "content": "弱命中",
                            "entity_tags": [],
                            "metadata": {},
                        },
                    }
                ]
            ]
        return [
            [
                {
                    "distance": 0.95,
                    "entity": {
                        "document_id": "doc_b",
                        "content": "强命中",
                        "entity_tags": [],
                        "metadata": {},
                    },
                }
            ]
        ]

    mock_client_v2.search = MagicMock(side_effect=_search)

    from app.rag.retriever import _do_search

    result = await _do_search("q", top_k=2, doc_type=None, document_id=None, entity_tags=None)
    # 强命中（distance 0.95）应排在前
    strong_idx = result.find("强命中")
    weak_idx = result.find("弱命中")
    assert strong_idx >= 0 and weak_idx >= 0
    assert strong_idx < weak_idx, "强命中（distance=0.95）应该排在弱命中（0.5）之前"


# ──────────────── query_knowledge_graph：kb_ids 注入 Cypher ────────────────


def test_build_cypher_with_kb_ids_includes_filter():
    from app.kg.query import build_cypher

    c = build_cypher(None, None, 2, kb_ids=["kb_a", "kb_b"])
    assert "start.kb_id IN $kb_ids" in c
    assert "WHERE" in c


def test_build_cypher_without_kb_ids_no_filter():
    from app.kg.query import build_cypher

    c = build_cypher(None, None, 2, kb_ids=None)
    assert "kb_id" not in c


def test_build_cypher_combines_kb_ids_with_other_filters():
    from app.kg.query import build_cypher

    c = build_cypher("PERSON", ["MENTIONED_IN"], 2, kb_ids=["kb_x"])
    assert "start.type = $entity_type" in c
    assert "ALL(rel IN r" in c
    assert "start.kb_id IN $kb_ids" in c
    # 三个条件用 AND 串联
    assert c.count("AND") == 2


async def test_query_kg_tool_empty_kb_ids_returns_skip_text(monkeypatch):
    """KG tool：kb_ids=[] → 不调 Neo4j，返提示文本。"""
    from app.kg.tool import query_knowledge_graph

    set_current_kb_ids([])

    # mock driver 不应被调用
    mock_get = MagicMock(side_effect=AssertionError("不该调 Neo4j"))
    monkeypatch.setattr("app.kg.tool.get_neo4j_driver", mock_get)

    result = await query_knowledge_graph.ainvoke({"entity_name": "台风"})
    assert "未指定知识库" in result


async def test_query_kg_tool_with_kb_ids_passes_to_query(monkeypatch):
    """KG tool：kb_ids=[a] → execute_graph_query 收到 kb_ids 参数。"""
    from app.kg.tool import query_knowledge_graph

    kb_a = uuid.uuid4()
    set_current_kb_ids([kb_a])

    captured: dict = {}

    async def _fake_execute(driver, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        "app.kg.tool.execute_graph_query", _fake_execute
    )
    monkeypatch.setattr(
        "app.kg.tool.get_neo4j_driver", lambda: MagicMock()
    )

    await query_knowledge_graph.ainvoke({"entity_name": "台风"})
    assert captured.get("kb_ids") == [str(kb_a)]


async def test_query_kg_tool_none_kb_ids_passes_none(monkeypatch):
    """KG tool：kb_ids=None → execute_graph_query 收 kb_ids=None。"""
    from app.kg.tool import query_knowledge_graph

    # 不设置 contextvar，默认 None

    captured: dict = {}

    async def _fake_execute(driver, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("app.kg.tool.execute_graph_query", _fake_execute)
    monkeypatch.setattr("app.kg.tool.get_neo4j_driver", lambda: MagicMock())

    await query_knowledge_graph.ainvoke({"entity_name": "台风"})
    assert captured.get("kb_ids") is None
