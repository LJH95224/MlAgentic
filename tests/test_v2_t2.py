"""V2.0 T2 阶段单测（混合检索引擎验收）。

覆盖：
1. V2 Schema BM25 Function + enable_analyzer
2. hybrid_retriever.hybrid_search 核心逻辑
3. RRF 降级策略
4. 格式化输出
5. ingest_task Step 10 确认步骤
6. V1.5 零回归
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymilvus import DataType

from app.ingest.parser import StructuredBlock
from app.ingest.structured_splitter import StructuredChunk
from app.rag.schema import build_v2_kb_collection_schema, build_v2_index_params


# ════════════════════════════════════════════════════════════════
# 1. V2 Schema BM25 Function
# ════════════════════════════════════════════════════════════════


class TestV2SchemaBM25:
    """V2 Schema 的 BM25 Function 配置验证。"""

    def test_content_field_has_enable_analyzer(self):
        """content 字段必须启用 enable_analyzer（BM25 Function 必需）。"""
        schema = build_v2_kb_collection_schema()
        content_field = None
        for f in schema.fields:
            if f.name == "content":
                content_field = f
                break
        assert content_field is not None, "content 字段未找到"
        assert content_field.params.get("enable_analyzer") is True

    def test_schema_has_bm25_function(self):
        """Schema 必须包含 BM25 Function。"""
        schema = build_v2_kb_collection_schema()
        functions = schema.functions
        assert len(functions) >= 1, "Schema 应至少包含一个 Function"

        bm25_fn = functions[0]
        assert bm25_fn.name == "bm25_fn"
        from pymilvus import FunctionType
        assert bm25_fn.type == FunctionType.BM25
        assert "content" in bm25_fn.input_field_names
        assert "sparse_vector" in bm25_fn.output_field_names

    def test_sparse_vector_field_exists(self):
        """sparse_vector 字段必须存在。"""
        schema = build_v2_kb_collection_schema()
        names = {f.name for f in schema.fields}
        assert "sparse_vector" in names

    def test_sparse_vector_is_sparse_float(self):
        """sparse_vector 类型必须是 SPARSE_FLOAT_VECTOR。"""
        schema = build_v2_kb_collection_schema()
        for f in schema.fields:
            if f.name == "sparse_vector":
                assert f.dtype == DataType.SPARSE_FLOAT_VECTOR
                return
        pytest.fail("sparse_vector 字段未找到")

    def test_total_field_count_15(self):
        """V2 Schema 仍为 15 个字段。"""
        schema = build_v2_kb_collection_schema()
        assert len(schema.fields) == 15

    def test_v2_index_params_bm25_params(self):
        """V2 索引参数应包含 BM25 的 k1/b 参数。"""
        mock_client = MagicMock()
        mock_index_params = MagicMock()
        mock_client.prepare_index_params.return_value = mock_index_params

        build_v2_index_params(mock_client)

        calls = {c.kwargs.get("field_name"): c.kwargs for c in mock_index_params.add_index.call_args_list}
        assert "sparse_vector" in calls
        params = calls["sparse_vector"]["params"]
        assert "bm25_k1" in params
        assert "bm25_b" in params
        assert params["bm25_k1"] == 1.2
        assert params["bm25_b"] == 0.75


# ════════════════════════════════════════════════════════════════
# 2. HybridSearchResult 数据类
# ════════════════════════════════════════════════════════════════


class TestHybridSearchResult:
    def test_creation(self):
        from app.rag.hybrid_retriever import HybridSearchResult

        result = HybridSearchResult(
            chunk_id=123,
            content="测试内容",
            document_id="doc-1",
            score=0.95,
            entity_tags=["台风"],
            heading_path=["第1章"],
            block_type="paragraph",
            page_number=5,
        )
        assert result.chunk_id == 123
        assert result.score == 0.95
        assert result.heading_path == ["第1章"]
        assert result.block_type == "paragraph"

    def test_defaults(self):
        from app.rag.hybrid_retriever import HybridSearchResult

        result = HybridSearchResult()
        assert result.content == ""
        assert result.entity_tags == []
        assert result.heading_path == []
        assert result.score == 0.0


# ════════════════════════════════════════════════════════════════
# 3. hybrid_search 核心逻辑（mock）
# ════════════════════════════════════════════════════════════════


class TestHybridSearch:
    """验证 hybrid_search 的检索流程（mock Milvus + Embedding）。"""

    @pytest.mark.asyncio
    async def test_hybrid_search_bm25_enabled(self):
        """BM25 启用时走 hybrid_search 双路检索。"""
        from app.rag.hybrid_retriever import hybrid_search

        # mock settings
        with patch("app.rag.hybrid_retriever.get_settings") as mock_settings, \
             patch("app.rag.hybrid_retriever.get_milvus_client") as mock_client_fn, \
             patch("app.rag.hybrid_retriever.get_current_role", return_value="ALL"), \
             patch("app.rag.hybrid_retriever.get_current_kb_ids", return_value=[uuid.uuid4()]), \
             patch("app.rag.hybrid_retriever.aembed_texts", new_callable=AsyncMock) as mock_embed:

            settings = MagicMock()
            settings.bm25_enable = True
            settings.rrf_k = 60
            settings.milvus_collection = "knowledge_chunks"
            mock_settings.return_value = settings

            mock_embed.return_value = [[0.1] * 4096]

            mock_client = MagicMock()
            mock_client.has_collection.return_value = True
            # hybrid_search 返回
            mock_client.hybrid_search.return_value = [
                [
                    {"distance": 0.95, "entity": {
                        "chunk_id": 1, "content": "台风", "document_id": "d1",
                        "entity_tags": ["台风"], "heading_path": ["第1章"],
                        "block_type": "paragraph", "page_number": None,
                        "metadata": {},
                    }},
                ]
            ]
            mock_client_fn.return_value = mock_client

            results = await hybrid_search("台风", top_k=5)

            assert len(results) == 1
            assert results[0].content == "台风"
            assert results[0].score == 0.95
            assert mock_client.hybrid_search.called

    @pytest.mark.asyncio
    async def test_hybrid_search_bm25_disabled_falls_back_to_dense(self):
        """BM25 禁用时退化为纯向量检索。"""
        from app.rag.hybrid_retriever import hybrid_search

        with patch("app.rag.hybrid_retriever.get_settings") as mock_settings, \
             patch("app.rag.hybrid_retriever.get_milvus_client") as mock_client_fn, \
             patch("app.rag.hybrid_retriever.get_current_role", return_value="ALL"), \
             patch("app.rag.hybrid_retriever.get_current_kb_ids", return_value=[uuid.uuid4()]), \
             patch("app.rag.hybrid_retriever.aembed_texts", new_callable=AsyncMock) as mock_embed:

            settings = MagicMock()
            settings.bm25_enable = False
            settings.rrf_k = 60
            settings.milvus_collection = "knowledge_chunks"
            mock_settings.return_value = settings

            mock_embed.return_value = [[0.1] * 4096]

            mock_client = MagicMock()
            mock_client.has_collection.return_value = True
            mock_client.search.return_value = [
                [{"distance": 0.88, "entity": {
                    "chunk_id": 2, "content": "降雨", "document_id": "d2",
                    "entity_tags": [], "heading_path": [],
                    "block_type": "paragraph", "page_number": None,
                    "metadata": {},
                }}]
            ]
            mock_client_fn.return_value = mock_client

            results = await hybrid_search("降雨", top_k=5)

            assert len(results) == 1
            assert results[0].content == "降雨"
            # BM25 禁用时应走 search 而非 hybrid_search
            assert mock_client.search.called
            assert not mock_client.hybrid_search.called

    @pytest.mark.asyncio
    async def test_hybrid_search_kb_ids_empty_returns_empty(self):
        """kb_ids=[] 时直接返回空列表。"""
        from app.rag.hybrid_retriever import hybrid_search

        with patch("app.rag.hybrid_retriever.get_settings") as mock_settings, \
             patch("app.rag.hybrid_retriever.get_current_kb_ids", return_value=[]):

            settings = MagicMock()
            settings.milvus_collection = "knowledge_chunks"
            mock_settings.return_value = settings

            results = await hybrid_search("测试", top_k=5)
            assert results == []

    @pytest.mark.asyncio
    async def test_hybrid_search_fallback_on_failure(self):
        """hybrid_search 失败时降级为纯向量检索。"""
        from app.rag.hybrid_retriever import hybrid_search

        with patch("app.rag.hybrid_retriever.get_settings") as mock_settings, \
             patch("app.rag.hybrid_retriever.get_milvus_client") as mock_client_fn, \
             patch("app.rag.hybrid_retriever.get_current_role", return_value="ALL"), \
             patch("app.rag.hybrid_retriever.get_current_kb_ids", return_value=[uuid.uuid4()]), \
             patch("app.rag.hybrid_retriever.aembed_texts", new_callable=AsyncMock) as mock_embed:

            settings = MagicMock()
            settings.bm25_enable = True
            settings.rrf_k = 60
            settings.milvus_collection = "knowledge_chunks"
            mock_settings.return_value = settings

            mock_embed.return_value = [[0.1] * 4096]

            mock_client = MagicMock()
            mock_client.has_collection.return_value = True
            # hybrid_search 抛异常
            mock_client.hybrid_search.side_effect = RuntimeError("BM25 not ready")
            # 降级 search 成功
            mock_client.search.return_value = [
                [{"distance": 0.80, "entity": {
                    "chunk_id": 3, "content": "降级结果", "document_id": "d3",
                    "entity_tags": [], "heading_path": [],
                    "block_type": "paragraph", "page_number": None,
                    "metadata": {},
                }}]
            ]
            mock_client_fn.return_value = mock_client

            results = await hybrid_search("测试", top_k=5)

            assert len(results) == 1
            assert results[0].content == "降级结果"


# ════════════════════════════════════════════════════════════════
# 4. 格式化输出
# ════════════════════════════════════════════════════════════════


class TestFormatHybridResults:
    def test_empty_results(self):
        from app.rag.hybrid_retriever import HybridSearchResult, format_hybrid_results

        result = format_hybrid_results([])
        assert "无结果" in result

    def test_format_with_heading(self):
        from app.rag.hybrid_retriever import HybridSearchResult, format_hybrid_results

        results = [
            HybridSearchResult(
                content="测试内容",
                document_id="d1",
                score=0.95,
                heading_path=["第1章", "1.1 节"],
                page_number=5,
            )
        ]
        text = format_hybrid_results(results)
        assert "第1章" in text
        assert "1.1 节" in text
        assert "p5" in text
        assert "score=0.950" in text

    def test_format_minimal(self):
        from app.rag.hybrid_retriever import HybridSearchResult, format_hybrid_results

        results = [
            HybridSearchResult(content="简单", document_id="d2", score=0.5)
        ]
        text = format_hybrid_results(results)
        assert "简单" in text
        assert "d2" in text


# ════════════════════════════════════════════════════════════════
# 5. Ingest Step 10 确认
# ════════════════════════════════════════════════════════════════


class TestIngestStep10:
    def test_step_bm25_auto_no_error(self):
        """Step 10 确认步骤不报错。"""
        from app.tasks.ingest_task import _step_bm25_auto

        _step_bm25_auto()  # 不抛异常即通过


# ════════════════════════════════════════════════════════════════
# 6. V2 Milvus 写入不含 sparse_vector
# ════════════════════════════════════════════════════════════════


class TestV2MilvusWriteNoSparse:
    """验证 V2 Milvus 写入行不含手动 sparse_vector 字段。"""

    def test_row_does_not_contain_sparse_vector(self):
        from app.tasks.ingest_task import _step_milvus_write_v2

        mock_resources = MagicMock()
        mock_milvus = MagicMock()
        mock_milvus.has_collection.return_value = True
        mock_resources.milvus = mock_milvus

        mock_kb = MagicMock()
        mock_kb.id = uuid.uuid4()
        mock_kb.embedding_dim = 4096

        mock_file = MagicMock()
        mock_file.id = uuid.uuid4()
        mock_file.filename = "test.pdf"
        mock_file.mime_type = "application/pdf"

        chunks = [
            StructuredChunk(
                chunk_id="chunk_0",
                index=0,
                content="BM25 测试内容",
                heading_path=["标题"],
                block_type="paragraph",
                page_number=None,
                position_index=0,
                parent_chunk_id=None,
                is_summary=False,
            )
        ]
        vectors = [[0.1] * 4096]

        _step_milvus_write_v2(
            mock_resources,
            kb=mock_kb,
            file_record=mock_file,
            chunks=chunks,
            vectors=vectors,
        )

        call_args = mock_milvus.upsert.call_args
        rows = call_args.kwargs.get("data") or call_args[1].get("data")
        assert len(rows) == 1
        row = rows[0]

        # sparse_vector 不应在写入数据中（Milvus BM25 Function 自动生成）
        assert "sparse_vector" not in row
        # 但 V2 结构字段应该存在
        assert "heading_path" in row
        assert "block_type" in row
