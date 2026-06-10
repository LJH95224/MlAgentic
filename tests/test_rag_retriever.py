"""Retriever 单测：mock Milvus + mock Embedding，纯逻辑验证。

覆盖：
- 过滤表达式拼装（RAG-04 权限基线、可选 doc_type、可选 document_id）
- 结果格式化（带/不带 entity_tags、空命中提示）
- 端到端 _do_search 调用链：embedding → search → format
- 异常透传（Milvus 报错不被吞）
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.rag.retriever import (
    _build_filter_expr,
    _do_search,
    _format_hits,
    search_knowledge_base,
)


# ──────────────────── _build_filter_expr ────────────────────


class TestBuildFilterExpr:
    def test_only_role_baseline(self):
        """无可选过滤时，仅有权限基线（RAG-04）。"""
        expr = _build_filter_expr(
            doc_type=None, document_id=None, entity_tags=None, current_role="ALL"
        )
        assert expr == 'ARRAY_CONTAINS(allowed_roles, "ALL")'

    def test_with_doc_type(self):
        """带 doc_type 时叠加 metadata 过滤（RAG-03）。"""
        expr = _build_filter_expr(
            doc_type="report", document_id=None, entity_tags=None, current_role="ALL"
        )
        assert 'ARRAY_CONTAINS(allowed_roles, "ALL")' in expr
        assert 'metadata["type"] == "report"' in expr
        assert " and " in expr

    def test_with_document_id(self):
        """带 document_id 时叠加 document_id 等值过滤。"""
        expr = _build_filter_expr(
            doc_type=None, document_id="doc_xyz", entity_tags=None, current_role="ALL"
        )
        assert 'document_id == "doc_xyz"' in expr

    def test_with_entity_tags(self):
        """带 entity_tags 时叠加 ARRAY_CONTAINS_ANY 过滤（KG-04 联合）。"""
        expr = _build_filter_expr(
            doc_type=None,
            document_id=None,
            entity_tags=["台风", "副热带高压"],
            current_role="ALL",
        )
        assert "ARRAY_CONTAINS_ANY(entity_tags," in expr
        assert '"台风"' in expr
        assert '"副热带高压"' in expr

    def test_with_all_optional(self):
        """四个条件同时存在，用 and 连接，子句数为 4。"""
        expr = _build_filter_expr(
            doc_type="paper",
            document_id="d1",
            entity_tags=["x"],
            current_role="ADMIN",
        )
        parts = expr.split(" and ")
        assert len(parts) == 4
        assert 'ARRAY_CONTAINS(allowed_roles, "ADMIN")' in parts
        assert 'metadata["type"] == "paper"' in parts
        assert 'document_id == "d1"' in parts

    def test_custom_role(self):
        """role 不是 ALL 时正确替换（未来权限体系接入预演）。"""
        expr = _build_filter_expr(
            None, None, None, current_role="ANALYST"
        )
        assert 'ARRAY_CONTAINS(allowed_roles, "ANALYST")' in expr


# ──────────────────── _format_hits ────────────────────


class TestFormatHits:
    def test_empty_hits_returns_hint(self):
        """空结果返回友好提示文本，而非空字符串。"""
        out = _format_hits([])
        assert "无结果" in out

    def test_single_hit_basic(self):
        """单条命中：包含序号 / score / doc 标识 / content。"""
        hits = [
            {
                "distance": 0.872,
                "entity": {
                    "document_id": "typhoon_2024",
                    "content": "台风路径预报示例",
                    "entity_tags": [],
                },
            }
        ]
        out = _format_hits(hits)
        assert "[1]" in out
        assert "0.872" in out
        assert "doc=typhoon_2024" in out
        assert "台风路径预报示例" in out
        # 空 tags 时不应输出 tags=
        assert "tags=" not in out

    def test_hit_with_tags(self):
        """带 entity_tags 时格式化输出。"""
        hits = [
            {
                "distance": 0.5,
                "entity": {
                    "document_id": "d",
                    "content": "x",
                    "entity_tags": ["台风", "海洋"],
                },
            }
        ]
        out = _format_hits(hits)
        assert "tags=[台风,海洋]" in out

    def test_multiple_hits_numbered(self):
        """多条命中：自动从 1 起编号。"""
        hits = [
            {"distance": 0.9, "entity": {"document_id": "a", "content": "AAA"}},
            {"distance": 0.8, "entity": {"document_id": "b", "content": "BBB"}},
        ]
        out = _format_hits(hits)
        assert "[1]" in out
        assert "[2]" in out
        assert "AAA" in out
        assert "BBB" in out


# ──────────────────── _do_search 端到端（mock） ────────────────────


@pytest.fixture
def mock_settings(monkeypatch):
    """mock settings：返回固定的 collection 名与 role。"""
    from app.core import config

    fake = MagicMock()
    fake.milvus_collection = "knowledge_chunks"
    fake.rag_default_role = "ALL"
    fake.embedding_dimension = 4096

    monkeypatch.setattr(config, "get_settings", lambda: fake)
    # retriever 模块自己也 import 了 get_settings，要同步 patch
    monkeypatch.setattr("app.rag.retriever.get_settings", lambda: fake)
    return fake


@pytest.fixture
def mock_embed(monkeypatch):
    """mock aembed_texts：固定返回 4096 维零向量。"""
    fake_vec = [0.0] * 4096
    mock = AsyncMock(return_value=[fake_vec])
    monkeypatch.setattr("app.rag.retriever.aembed_texts", mock)
    return mock


@pytest.fixture
def mock_client(monkeypatch):
    """mock get_milvus_client：返回 search 可被预设的 mock 对象。"""
    client = MagicMock()
    # 默认返回单条命中
    client.search.return_value = [
        [
            {
                "distance": 0.91,
                "entity": {
                    "document_id": "doc_a",
                    "content": "示例片段",
                    "entity_tags": ["气象"],
                    "metadata": {"type": "report"},
                },
            }
        ]
    ]
    monkeypatch.setattr("app.rag.retriever.get_milvus_client", lambda: client)
    return client


@pytest.mark.asyncio
async def test_do_search_basic_calls_milvus_with_role_filter(
    mock_settings, mock_embed, mock_client
):
    """基础检索：filter 必含权限基线（RAG-04）。"""
    out = await _do_search(
        query="台风", top_k=5, doc_type=None, document_id=None, entity_tags=None
    )

    # embedding 被调用
    mock_embed.assert_awaited_once_with(["台风"])

    # milvus.search 被调用一次
    assert mock_client.search.call_count == 1
    call_kwargs = mock_client.search.call_args.kwargs

    # 校验关键 kwargs
    assert call_kwargs["collection_name"] == "knowledge_chunks"
    assert call_kwargs["limit"] == 5
    assert call_kwargs["data"] == [[0.0] * 4096]
    assert 'ARRAY_CONTAINS(allowed_roles, "ALL")' in call_kwargs["filter"]
    # 输出字段必须含 document_id 与 entity_tags（RAG-05）
    assert "document_id" in call_kwargs["output_fields"]
    assert "entity_tags" in call_kwargs["output_fields"]
    # 度量类型 COSINE
    assert call_kwargs["search_params"]["metric_type"] == "COSINE"

    # 输出包含命中内容
    assert "示例片段" in out


@pytest.mark.asyncio
async def test_do_search_with_doc_type_filter(
    mock_settings, mock_embed, mock_client
):
    """带 doc_type 时 filter 含 metadata 子句（RAG-03）。"""
    await _do_search(
        query="x", top_k=3, doc_type="report", document_id=None, entity_tags=None
    )
    expr = mock_client.search.call_args.kwargs["filter"]
    assert 'metadata["type"] == "report"' in expr


@pytest.mark.asyncio
async def test_do_search_with_document_id_filter(
    mock_settings, mock_embed, mock_client
):
    """带 document_id 时 filter 含 document_id 等值子句。"""
    await _do_search(
        query="x", top_k=3, doc_type=None, document_id="d42", entity_tags=None
    )
    expr = mock_client.search.call_args.kwargs["filter"]
    assert 'document_id == "d42"' in expr


@pytest.mark.asyncio
async def test_do_search_with_entity_tags(mock_settings, mock_embed, mock_client):
    """带 entity_tags 时 filter 含 ARRAY_CONTAINS_ANY 子句（KG-04 联合）。"""
    await _do_search(
        query="x", top_k=3, doc_type=None, document_id=None,
        entity_tags=["台风", "ECMWF"],
    )
    expr = mock_client.search.call_args.kwargs["filter"]
    assert "ARRAY_CONTAINS_ANY(entity_tags," in expr
    assert '"台风"' in expr
    assert '"ECMWF"' in expr


@pytest.mark.asyncio
async def test_do_search_clamps_top_k(mock_settings, mock_embed, mock_client):
    """top_k 越界（>50）应被夹到 50；非法值（<1）应回退到 5。"""
    await _do_search(
        query="x", top_k=999, doc_type=None, document_id=None, entity_tags=None
    )
    assert mock_client.search.call_args.kwargs["limit"] == 50

    mock_client.reset_mock()
    await _do_search(
        query="x", top_k=0, doc_type=None, document_id=None, entity_tags=None
    )
    assert mock_client.search.call_args.kwargs["limit"] == 5


@pytest.mark.asyncio
async def test_do_search_exception_propagates(mock_settings, mock_embed, mock_client):
    """Milvus 抛错时不应被本层吞掉（AGT-04 错误反思链路依赖）。"""
    mock_client.search.side_effect = RuntimeError("milvus down")
    with pytest.raises(RuntimeError, match="milvus down"):
        await _do_search(
            query="x", top_k=5, doc_type=None, document_id=None, entity_tags=None
        )


# ──────────────────── @tool 装饰器集成（RAG-02 验收） ────────────────────


def test_tool_name_and_args_schema_for_llm():
    """@tool 装饰后必须能正确暴露 name 与参数 schema 供 LLM 推断。"""
    assert search_knowledge_base.name == "search_knowledge_base"

    schema = search_knowledge_base.args_schema.model_json_schema()
    props = schema["properties"]
    # PRD RAG-02 要求至少 query / top_k
    assert "query" in props
    assert "top_k" in props
    # 标量过滤参数（RAG-03）
    assert "doc_type" in props
    assert "document_id" in props
    # KG-04 联合查询参数
    assert "entity_tags" in props


def test_tool_registered_in_tool_map():
    """search_knowledge_base 已挂到工具注册中心。"""
    from app.tools import get_tool_map

    tool_map = get_tool_map()
    assert "search_knowledge_base" in tool_map
    assert tool_map["search_knowledge_base"] is search_knowledge_base
