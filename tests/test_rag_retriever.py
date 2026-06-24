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

    def test_string_literals_are_escaped(self):
        """Milvus filter 中的双引号与反斜杠必须转义，避免表达式注入或语法错误。"""
        expr = _build_filter_expr(
            doc_type='report"2026',
            document_id='doc"abc',
            entity_tags=['台风"路径', 'C:\\data'],
            current_role='ROLE"A',
        )
        assert 'ARRAY_CONTAINS(allowed_roles, "ROLE\\"A")' in expr
        assert 'metadata["type"] == "report\\"2026"' in expr
        assert 'document_id == "doc\\"abc"' in expr
        assert '"台风\\"路径"' in expr
        assert '"C:\\\\data"' in expr


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
    # get_current_role 走 app.rag.filters.get_settings（P2-13 后 retriever 不再
    # 直接 import get_settings，只 re-export filters 中的工具函数）
    monkeypatch.setattr("app.rag.filters.get_settings", lambda: fake)
    return fake


@pytest.fixture
def mock_hybrid_search(monkeypatch):
    """mock hybrid_search：验证 Agent Tool 复用 V2 混合检索链路。"""
    from app.rag.hybrid_retriever import HybridSearchResult

    mock = AsyncMock(return_value=[
        HybridSearchResult(
            chunk_id=1,
            content="示例片段",
            document_id="doc_a",
            score=0.91,
            entity_tags=["气象"],
            heading_path=["第一章"],
            page_number=2,
            metadata={"type": "report"},
        )
    ])
    monkeypatch.setattr("app.rag.hybrid_retriever.hybrid_search", mock)
    return mock


@pytest.mark.asyncio
async def test_do_search_uses_v2_hybrid_search(mock_settings, mock_hybrid_search):
    """Agent Tool 复用 V2 hybrid_search，获得 BM25/RRF/Reranker 能力。"""
    out = await _do_search(
        query="台风", top_k=5, doc_type=None, document_id=None, entity_tags=None
    )

    mock_hybrid_search.assert_awaited_once_with(
        query="台风",
        top_k=5,
        doc_type=None,
        document_id=None,
        entity_tags=None,
    )
    assert "示例片段" in out
    assert "heading=第一章" in out


@pytest.mark.asyncio
async def test_do_search_passes_filters_to_hybrid_search(mock_settings, mock_hybrid_search):
    """doc_type / document_id / entity_tags 原样透传给 V2 检索链路。"""
    await _do_search(
        query="x", top_k=3, doc_type="report", document_id="d42",
        entity_tags=["台风", "ECMWF"],
    )
    mock_hybrid_search.assert_awaited_once_with(
        query="x",
        top_k=3,
        doc_type="report",
        document_id="d42",
        entity_tags=["台风", "ECMWF"],
    )


@pytest.mark.asyncio
async def test_do_search_clamps_top_k(mock_settings, mock_hybrid_search):
    """top_k 越界（>50）应被夹到 50；非法值（<1）应回退到 5。"""
    await _do_search(
        query="x", top_k=999, doc_type=None, document_id=None, entity_tags=None
    )
    assert mock_hybrid_search.call_args.kwargs["top_k"] == 50

    mock_hybrid_search.reset_mock()
    await _do_search(
        query="x", top_k=0, doc_type=None, document_id=None, entity_tags=None
    )
    assert mock_hybrid_search.call_args.kwargs["top_k"] == 5


@pytest.mark.asyncio
async def test_do_search_top_k_boundary_values_preserved(mock_settings, mock_hybrid_search):
    """B M-07：合法边界值 top_k=1 / top_k=50 不应被 clamp 改动。"""
    # 下边界：1 是合法最小值
    await _do_search(
        query="x", top_k=1, doc_type=None, document_id=None, entity_tags=None
    )
    assert mock_hybrid_search.call_args.kwargs["top_k"] == 1

    # 上边界：50 是合法最大值
    mock_hybrid_search.reset_mock()
    await _do_search(
        query="x", top_k=50, doc_type=None, document_id=None, entity_tags=None
    )
    assert mock_hybrid_search.call_args.kwargs["top_k"] == 50

    # 负数：与 0 同款，回退默认 5
    mock_hybrid_search.reset_mock()
    await _do_search(
        query="x", top_k=-3, doc_type=None, document_id=None, entity_tags=None
    )
    assert mock_hybrid_search.call_args.kwargs["top_k"] == 5

    # 51：刚越上界，clamp 到 50
    mock_hybrid_search.reset_mock()
    await _do_search(
        query="x", top_k=51, doc_type=None, document_id=None, entity_tags=None
    )
    assert mock_hybrid_search.call_args.kwargs["top_k"] == 50


@pytest.mark.asyncio
async def test_do_search_returns_empty_kb_hint_without_hybrid_call(
    mock_settings, mock_hybrid_search, monkeypatch
):
    """kb_ids=[] 时不查任何 Collection。"""
    monkeypatch.setattr("app.rag.retriever.get_current_kb_ids", lambda: [])
    result = await _do_search(
        query="x", top_k=5, doc_type=None, document_id=None, entity_tags=None
    )
    assert "未指定知识库" in result
    mock_hybrid_search.assert_not_awaited()


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
