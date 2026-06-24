"""基础设施扩展验收单测。

覆盖：
1. 配置项默认值 + 环境变量覆盖
2. AgentTrace / EvalTask 新 PG 模型字段
3. KB / KbFile 扩展字段
4. Milvus Schema 字段定义 + 稀疏向量 + BM25 索引
5. 现有功能零回归
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from pymilvus import DataType

from tests.conftest import skip_without_db


# ─────────── 工具函数 ───────────


def _fresh_settings(**env_overrides):
    """清空 lru_cache + 设环境变量 + 重新拿一个 Settings 实例。"""
    for k, v in env_overrides.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    from app.core.config import get_settings

    get_settings.cache_clear()
    return get_settings()


def _fresh_settings_no_env_file(**env_overrides):
    """忽略 .env 文件，仅用进程环境变量验证 Settings 默认值 / 覆盖。"""
    for k, v in env_overrides.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    from app.core.config import Settings, get_settings

    get_settings.cache_clear()
    return Settings(_env_file=None)


def _cleanup(*keys):
    for k in keys:
        os.environ.pop(k, None)
    from app.core.config import get_settings

    get_settings.cache_clear()


def _field_by_name(schema, name: str):
    """从 CollectionSchema 中按名取 FieldSchema。"""
    for f in schema.fields:
        if f.name == name:
            return f
    raise AssertionError(f"字段 {name!r} 未在 Schema 中找到")


# ════════════════════════════════════════════════════════════════
# 1. 配置项
# ════════════════════════════════════════════════════════════════


class TestV2Settings:
    """新增 Settings 字段默认值 + 覆盖行为。"""

    # ── 应用基础 / CORS ──

    def test_cors_allow_origins_default_empty(self):
        try:
            s = _fresh_settings_no_env_file(CORS_ALLOW_ORIGINS=None)
            assert s.cors_allow_origins == ""
        finally:
            _cleanup("CORS_ALLOW_ORIGINS")

    def test_cors_allow_origins_override(self):
        origins = "http://localhost:3000,http://127.0.0.1:5173"
        try:
            s = _fresh_settings_no_env_file(CORS_ALLOW_ORIGINS=origins)
            assert s.cors_allow_origins == origins
        finally:
            _cleanup("CORS_ALLOW_ORIGINS")

    def test_cors_allow_credentials_default_false(self):
        try:
            s = _fresh_settings_no_env_file(CORS_ALLOW_CREDENTIALS=None)
            assert s.cors_allow_credentials is False
        finally:
            _cleanup("CORS_ALLOW_CREDENTIALS")

    def test_cors_allow_credentials_override(self):
        try:
            s = _fresh_settings_no_env_file(CORS_ALLOW_CREDENTIALS="true")
            assert s.cors_allow_credentials is True
        finally:
            _cleanup("CORS_ALLOW_CREDENTIALS")

    # ── Reranker ──
    # 注：当 .env 中有 RERANKER_* 配置时，"默认值"测试会读到 .env 的值
    # 而非代码中 Field(default=...)。因此这些测试验证的是"设空后回落到默认值"。

    def test_reranker_type_default_none(self):
        s = _fresh_settings(RERANKER_TYPE="none")
        assert s.reranker_type == "none"
        _cleanup("RERANKER_TYPE")

    def test_reranker_type_override(self):
        s = _fresh_settings(RERANKER_TYPE="api")
        assert s.reranker_type == "api"
        _cleanup("RERANKER_TYPE")

    def test_reranker_model_default_none(self):
        s = _fresh_settings(RERANKER_MODEL="")
        assert s.reranker_model is None or s.reranker_model == ""
        _cleanup("RERANKER_MODEL")

    def test_reranker_api_key_default_none(self):
        s = _fresh_settings(RERANKER_API_KEY="")
        assert s.reranker_api_key is None or s.reranker_api_key == ""
        _cleanup("RERANKER_API_KEY")

    def test_reranker_api_base_default_none(self):
        s = _fresh_settings(RERANKER_API_BASE="")
        assert s.reranker_api_base is None or s.reranker_api_base == ""
        _cleanup("RERANKER_API_BASE")

    def test_reranker_similarity_threshold_default(self):
        s = _fresh_settings(RERANKER_SIMILARITY_THRESHOLD=None)
        assert s.reranker_similarity_threshold == 0.3
        _cleanup("RERANKER_SIMILARITY_THRESHOLD")

    def test_reranker_similarity_threshold_override(self):
        s = _fresh_settings(RERANKER_SIMILARITY_THRESHOLD="0.5")
        assert s.reranker_similarity_threshold == 0.5
        _cleanup("RERANKER_SIMILARITY_THRESHOLD")

    # ── BM25 ──

    def test_bm25_enable_default_true(self):
        s = _fresh_settings(BM25_ENABLE=None)
        assert s.bm25_enable is True
        _cleanup("BM25_ENABLE")

    def test_bm25_enable_override_false(self):
        s = _fresh_settings(BM25_ENABLE="false")
        assert s.bm25_enable is False
        _cleanup("BM25_ENABLE")

    def test_rrf_k_default_60(self):
        s = _fresh_settings(RRF_K=None)
        assert s.rrf_k == 60
        _cleanup("RRF_K")

    def test_rrf_k_override(self):
        s = _fresh_settings(RRF_K="100")
        assert s.rrf_k == 100
        _cleanup("RRF_K")

    # ── Trace ──

    def test_trace_enable_default_true(self):
        s = _fresh_settings(TRACE_ENABLE=None)
        assert s.trace_enable is True
        _cleanup("TRACE_ENABLE")

    def test_trace_enable_override_false(self):
        s = _fresh_settings(TRACE_ENABLE="false")
        assert s.trace_enable is False
        _cleanup("TRACE_ENABLE")

    def test_trace_retention_days_default_90(self):
        s = _fresh_settings(TRACE_RETENTION_DAYS=None)
        assert s.trace_retention_days == 90
        _cleanup("TRACE_RETENTION_DAYS")

    def test_trace_retention_days_override(self):
        s = _fresh_settings(TRACE_RETENTION_DAYS="30")
        assert s.trace_retention_days == 30
        _cleanup("TRACE_RETENTION_DAYS")


# ════════════════════════════════════════════════════════════════
# 2. AgentTrace 模型字段
# ════════════════════════════════════════════════════════════════


class TestAgentTraceModel:
    """AgentTrace 表字段校验（OBS-01）。"""

    def test_table_name(self):
        from app.models.agent_trace import AgentTrace

        assert AgentTrace.__tablename__ == "agent_traces"

    def test_has_all_required_fields(self):
        """AgentTrace 必须包含 OBS-01 规定的全部字段。"""
        from app.models.agent_trace import AgentTrace

        cols = AgentTrace.__table__.columns
        required = {
            "id",
            "trace_id",
            "session_id",
            "kb_id",
            "step_type",
            "parent_step",
            "step_latency_ms",
            "total_latency_ms",
            "step_input",
            "step_output",
            "model_name",
            "token_count",
            "error_message",
            "created_at",
        }
        actual = set(cols.keys())
        assert required <= actual, f"缺少字段：{required - actual}"

    def test_trace_id_varchar_indexed(self):
        from app.models.agent_trace import AgentTrace

        col = AgentTrace.__table__.columns["trace_id"]
        assert col.type.length == 64
        assert col.nullable is False
        # 索引存在性：index=True 在列上声明，验证列上有 index 属性
        assert col.index is True

    def test_step_type_varchar_64(self):
        from app.models.agent_trace import AgentTrace

        col = AgentTrace.__table__.columns["step_type"]
        assert col.type.length == 64
        assert col.nullable is False

    def test_step_input_output_jsonb(self):
        from app.models.agent_trace import AgentTrace
        from sqlalchemy.dialects.postgresql import JSONB

        cols = AgentTrace.__table__.columns
        assert isinstance(cols["step_input"].type, JSONB)
        assert isinstance(cols["step_output"].type, JSONB)

    def test_nullable_fields(self):
        """session_id / kb_id / parent_step / 各计数 / error_message 允许为空。"""
        from app.models.agent_trace import AgentTrace

        cols = AgentTrace.__table__.columns
        nullable_fields = [
            "session_id",
            "kb_id",
            "parent_step",
            "step_latency_ms",
            "total_latency_ms",
            "step_input",
            "step_output",
            "model_name",
            "token_count",
            "error_message",
        ]
        for name in nullable_fields:
            assert cols[name].nullable is True, f"{name} 应该允许为空"


# ════════════════════════════════════════════════════════════════
# 3. EvalTask 模型字段
# ════════════════════════════════════════════════════════════════


class TestEvalTaskModel:
    """EvalTask 表字段校验（EVA-01/02/03）。"""

    def test_table_name(self):
        from app.models.eval_task import EvalTask

        assert EvalTask.__tablename__ == "eval_tasks"

    def test_has_all_required_fields(self):
        from app.models.eval_task import EvalTask

        cols = EvalTask.__table__.columns
        required = {
            "id",
            "kb_id",
            "name",
            "status",
            "progress",
            "eval_dataset",
            "eval_result",
            "eval_config",
            "question_count",
            "error_message",
            "created_at",
            "completed_at",
        }
        actual = set(cols.keys())
        assert required <= actual, f"缺少字段：{required - actual}"

    def test_status_default_pending(self):
        from app.models.eval_task import EVAL_STATUS_PENDING, EvalTask

        col = EvalTask.__table__.columns["status"]
        assert str(col.server_default.arg) == EVAL_STATUS_PENDING

    def test_status_choices_completeness(self):
        from app.models.eval_task import EVAL_STATUS_CHOICES

        assert set(EVAL_STATUS_CHOICES) == {"pending", "processing", "completed", "failed"}

    def test_progress_default_zero(self):
        from app.models.eval_task import EvalTask

        col = EvalTask.__table__.columns["progress"]
        assert str(col.server_default.arg) == "0"

    def test_eval_dataset_result_config_jsonb(self):
        from app.models.eval_task import EvalTask
        from sqlalchemy.dialects.postgresql import JSONB

        cols = EvalTask.__table__.columns
        assert isinstance(cols["eval_dataset"].type, JSONB)
        assert isinstance(cols["eval_result"].type, JSONB)
        assert isinstance(cols["eval_config"].type, JSONB)

    def test_kb_id_indexed(self):
        from app.models.eval_task import EvalTask

        col = EvalTask.__table__.columns["kb_id"]
        assert col.index is True


# ════════════════════════════════════════════════════════════════
# 4. KB / KbFile V2 扩展字段
# ════════════════════════════════════════════════════════════════


class TestKBV2Extensions:
    """对 KnowledgeBase 和 KbFile 的字段扩展。"""

    def test_knowledge_base_has_v2_fields(self):
        from app.models.knowledge_base import KnowledgeBase

        cols = KnowledgeBase.__table__.columns
        assert "retrieval_config" in cols, "KB 缺少 V2 字段：retrieval_config"
        assert "doc_metadata_schema" in cols, "KB 缺少 V2 字段：doc_metadata_schema"

    def test_knowledge_base_v2_fields_jsonb_nullable(self):
        from app.models.knowledge_base import KnowledgeBase
        from sqlalchemy.dialects.postgresql import JSONB

        cols = KnowledgeBase.__table__.columns
        # retrieval_config
        assert isinstance(cols["retrieval_config"].type, JSONB)
        assert cols["retrieval_config"].nullable is True
        # doc_metadata_schema
        assert isinstance(cols["doc_metadata_schema"].type, JSONB)
        assert cols["doc_metadata_schema"].nullable is True

    def test_kb_file_has_v2_fields(self):
        from app.models.kb_file import KbFile

        cols = KbFile.__table__.columns
        assert "doc_metadata" in cols, "KbFile 缺少 V2 字段：doc_metadata"
        assert "summary_brief" in cols, "KbFile 缺少 V2 字段：summary_brief"

    def test_kb_file_v2_fields_types(self):
        from app.models.kb_file import KbFile
        from sqlalchemy.dialects.postgresql import JSONB

        cols = KbFile.__table__.columns
        # doc_metadata
        assert isinstance(cols["doc_metadata"].type, JSONB)
        assert cols["doc_metadata"].nullable is True
        # summary_brief
        assert cols["summary_brief"].type.python_type in (str, type(None))
        assert cols["summary_brief"].nullable is True

    def test_legacy_v1_5_tables_generate_v2_compat_alter_sql(self):
        """旧 PG 表缺字段时，启动期兼容迁移应补齐列。

        真实故障表现：/api/v1/knowledge-bases 列表查询 ORM 全字段时，
        旧库缺 retrieval_config / doc_metadata_schema 会触发 UndefinedColumn → 500。
        """
        from app.main import _build_v2_compat_alter_sql

        existing = {
            "knowledge_bases": {"id", "name", "description"},
            "kb_files": {"id", "kb_id", "filename"},
        }

        sql = _build_v2_compat_alter_sql(existing)

        # V2.0 baseline 引入的列（retrieval_config / doc_metadata_schema / doc_metadata / summary_brief）
        assert (
            "ALTER TABLE knowledge_bases "
            "ADD COLUMN IF NOT EXISTS retrieval_config JSONB"
        ) in sql
        assert (
            "ALTER TABLE knowledge_bases "
            "ADD COLUMN IF NOT EXISTS doc_metadata_schema JSONB"
        ) in sql
        assert "ALTER TABLE kb_files ADD COLUMN IF NOT EXISTS doc_metadata JSONB" in sql
        assert "ALTER TABLE kb_files ADD COLUMN IF NOT EXISTS summary_brief TEXT" in sql

        # P1.9（2026-06-23）补：删除补偿状态 + 心跳锚点
        assert (
            "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS updated_at "
            "TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL"
        ) in sql
        assert (
            "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS "
            "cleanup_retry_count INTEGER DEFAULT 0 NOT NULL"
        ) in sql
        assert (
            "ALTER TABLE kb_files ADD COLUMN IF NOT EXISTS updated_at "
            "TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL"
        ) in sql
        assert (
            "ALTER TABLE kb_files ADD COLUMN IF NOT EXISTS "
            "cleanup_retry_count INTEGER DEFAULT 0 NOT NULL"
        ) in sql

    def test_existing_v2_columns_do_not_generate_alter_sql(self):
        """已是 V2 表结构时，兼容迁移必须幂等不执行 ALTER。"""
        from app.main import _build_v2_compat_alter_sql

        # 列完整的 V2.0 表（含 P1.9 后新加的 updated_at / cleanup_retry_count）
        existing = {
            "knowledge_bases": {
                "retrieval_config",
                "doc_metadata_schema",
                "updated_at",
                "cleanup_retry_count",
            },
            "kb_files": {
                "doc_metadata",
                "summary_brief",
                "updated_at",
                "cleanup_retry_count",
            },
        }

        assert _build_v2_compat_alter_sql(existing) == []


@skip_without_db
@pytest.mark.asyncio
async def test_v2_compat_migration_allows_orm_query_on_legacy_v1_5_tables():
    """旧表升级后，列表查询所需 ORM SELECT 不应再因缺列失败。"""
    import uuid

    from sqlalchemy import select, text

    from app.db.session import AsyncSessionLocal, engine
    from app.main import _ensure_v2_compat_columns
    from app.models.knowledge_base import KnowledgeBase

    from app.models.base import Base

    kb_id = uuid.uuid4()
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS kb_files"))
            await conn.execute(text("DROP TABLE IF EXISTS knowledge_bases"))
            await conn.execute(
                text(
                    """
                    CREATE TABLE knowledge_bases (
                        id UUID PRIMARY KEY,
                        name VARCHAR(128) NOT NULL UNIQUE,
                        description TEXT,
                        embedding_dim INTEGER NOT NULL DEFAULT 4096,
                        chunk_size INTEGER NOT NULL DEFAULT 512,
                        chunk_overlap INTEGER NOT NULL DEFAULT 64,
                        status VARCHAR(20) NOT NULL DEFAULT 'active',
                        file_count INTEGER NOT NULL DEFAULT 0,
                        chunk_count INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE TABLE kb_files (
                        id UUID PRIMARY KEY,
                        kb_id UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                        filename VARCHAR(512) NOT NULL,
                        file_path VARCHAR(1024) NOT NULL,
                        file_size BIGINT NOT NULL,
                        mime_type VARCHAR(128) NOT NULL,
                        status VARCHAR(20) NOT NULL DEFAULT 'pending',
                        progress INTEGER NOT NULL DEFAULT 0,
                        chunk_count INTEGER NOT NULL DEFAULT 0,
                        entity_count INTEGER NOT NULL DEFAULT 0,
                        error_message TEXT,
                        celery_task_id VARCHAR(255),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        completed_at TIMESTAMPTZ
                    )
                    """
                )
            )
            await conn.execute(
                text("INSERT INTO knowledge_bases (id, name) VALUES (:id, :name)"),
                {"id": kb_id, "name": "旧库兼容测试"},
            )

        await _ensure_v2_compat_columns()

        # 走 ORM 会话拿到真实模型对象（Core connection.execute 拿到的是 Row）
        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
                )
            ).scalar_one()
            assert row.name == "旧库兼容测试"
            assert row.retrieval_config is None
            assert row.doc_metadata_schema is None
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()


# ════════════════════════════════════════════════════════════════
# 5. V2 Milvus Schema
# ════════════════════════════════════════════════════════════════


class TestV2KBCollectionSchema:
    """KB Collection Schema 字段 + 稀疏向量 + BM25 索引。"""

    def test_v2_schema_has_v1_5_base_fields(self):
        """Schema 必须包含全部 8 个基础字段。"""
        from app.rag.schema import build_v2_kb_collection_schema

        schema = build_v2_kb_collection_schema()
        names = {f.name for f in schema.fields}
        v1_5_fields = {
            "chunk_id",
            "vector",
            "document_id",
            "content",
            "allowed_roles",
            "entity_tags",
            "metadata",
            "kb_id",
        }
        assert v1_5_fields <= names, f"Schema 缺少基础字段：{v1_5_fields - names}"

    def test_v2_schema_has_7_new_fields(self):
        """Schema 必须包含新增的 7 个字段。"""
        from app.rag.schema import build_v2_kb_collection_schema

        schema = build_v2_kb_collection_schema()
        names = {f.name for f in schema.fields}
        v2_fields = {
            "heading_path",
            "block_type",
            "page_number",
            "position_index",
            "parent_chunk_id",
            "is_summary",
            "sparse_vector",
        }
        assert v2_fields <= names, f"V2 缺少新字段：{v2_fields - names}"

    def test_v2_schema_total_field_count(self):
        """Schema 总共 15 个字段。"""
        from app.rag.schema import build_v2_kb_collection_schema

        schema = build_v2_kb_collection_schema()
        assert len(schema.fields) == 15

    def test_heading_path_array_varchar(self):
        """heading_path 是 VARCHAR ARRAY，capacity 10。"""
        from app.rag.schema import build_v2_kb_collection_schema

        schema = build_v2_kb_collection_schema()
        field = _field_by_name(schema, "heading_path")
        assert field.dtype == DataType.ARRAY
        assert field.element_type == DataType.VARCHAR
        assert field.params.get("max_capacity") == 10

    def test_block_type_varchar_32(self):
        from app.rag.schema import build_v2_kb_collection_schema

        schema = build_v2_kb_collection_schema()
        field = _field_by_name(schema, "block_type")
        assert field.dtype == DataType.VARCHAR
        assert field.params.get("max_length") == 32

    def test_page_number_int32_nullable(self):
        from app.rag.schema import build_v2_kb_collection_schema

        schema = build_v2_kb_collection_schema()
        field = _field_by_name(schema, "page_number")
        assert field.dtype == DataType.INT32
        assert field.nullable is True

    def test_position_index_int32(self):
        from app.rag.schema import build_v2_kb_collection_schema

        schema = build_v2_kb_collection_schema()
        field = _field_by_name(schema, "position_index")
        assert field.dtype == DataType.INT32

    def test_parent_chunk_id_varchar_nullable(self):
        from app.rag.schema import build_v2_kb_collection_schema

        schema = build_v2_kb_collection_schema()
        field = _field_by_name(schema, "parent_chunk_id")
        assert field.dtype == DataType.VARCHAR
        assert field.params.get("max_length") == 64
        assert field.nullable is True

    def test_is_summary_bool(self):
        from app.rag.schema import build_v2_kb_collection_schema

        schema = build_v2_kb_collection_schema()
        field = _field_by_name(schema, "is_summary")
        assert field.dtype == DataType.BOOL

    def test_sparse_vector_type(self):
        """sparse_vector 必须是 SPARSE_FLOAT_VECTOR。"""
        from app.rag.schema import build_v2_kb_collection_schema

        schema = build_v2_kb_collection_schema()
        field = _field_by_name(schema, "sparse_vector")
        assert field.dtype == DataType.SPARSE_FLOAT_VECTOR

    def test_v2_schema_dynamic_field_disabled(self):
        from app.rag.schema import build_v2_kb_collection_schema

        schema = build_v2_kb_collection_schema()
        assert schema.enable_dynamic_field is False

    def test_v2_schema_dim_customizable(self):
        """V2 Schema 也支持自定义向量维度。"""
        from app.rag.schema import build_v2_kb_collection_schema

        schema = build_v2_kb_collection_schema(dim=1024)
        vec = _field_by_name(schema, "vector")
        assert vec.params.get("dim") == 1024


# ════════════════════════════════════════════════════════════════
# 6. V2 索引参数
# ════════════════════════════════════════════════════════════════


class TestV2IndexParams:
    """索引参数（HNSW + SPARSE_INVERTED_INDEX BM25 + INVERTED）。"""

    def test_v2_index_params_has_sparse_bm25(self):
        """V2 索引必须包含 sparse_vector 上的 SPARSE_INVERTED_INDEX + BM25。"""
        from app.rag.schema import build_v2_index_params

        mock_client = MagicMock()
        mock_index_params = MagicMock()
        mock_client.prepare_index_params.return_value = mock_index_params

        build_v2_index_params(mock_client)

        # 验证 add_index 被调用 3 次：HNSW + sparse + INVERTED
        assert mock_index_params.add_index.call_count == 3

        # 提取每次调用的关键字参数
        calls = {c.kwargs.get("field_name"): c.kwargs for c in mock_index_params.add_index.call_args_list}

        # 稠密向量索引
        assert "vector" in calls
        assert calls["vector"]["index_type"] == "HNSW"
        assert calls["vector"]["metric_type"] == "COSINE"

        # 稀疏向量索引
        assert "sparse_vector" in calls
        assert calls["sparse_vector"]["index_type"] == "SPARSE_INVERTED_INDEX"
        assert calls["sparse_vector"]["metric_type"] == "BM25"

        # 标量索引
        assert "document_id" in calls
        assert calls["document_id"]["index_type"] == "INVERTED"

    def test_v2_index_params_drop_ratio_build(self):
        """稀疏向量索引的 drop_ratio_build 参数应设为 0.2。"""
        from app.rag.schema import build_v2_index_params

        mock_client = MagicMock()
        mock_index_params = MagicMock()
        mock_client.prepare_index_params.return_value = mock_index_params

        build_v2_index_params(mock_client)

        calls = {c.kwargs.get("field_name"): c.kwargs for c in mock_index_params.add_index.call_args_list}
        assert calls["sparse_vector"]["params"]["drop_ratio_build"] == 0.2


# ════════════════════════════════════════════════════════════════
# 7. 零回归
# ════════════════════════════════════════════════════════════════


class TestV15NoRegression:
    """验证 Schema 在改造后仍然正常。"""

    def test_v1_schema_still_works(self):
        """基线 Schema 未被破坏。"""
        from app.rag.schema import build_knowledge_chunks_schema

        schema = build_knowledge_chunks_schema()
        names = {f.name for f in schema.fields}
        assert names == {
            "chunk_id",
            "vector",
            "document_id",
            "content",
            "allowed_roles",
            "entity_tags",
            "metadata",
        }

    def test_v1_5_schema_still_works(self):
        """基础 Schema 未被破坏。"""
        from app.rag.schema import build_kb_collection_schema

        schema = build_kb_collection_schema()
        names = {f.name for f in schema.fields}
        assert "kb_id" in names
        assert len(schema.fields) == 8  # 基础 7 + kb_id

    def test_v1_index_params_still_works(self):
        """基础索引参数仍正常（只有 HNSW + INVERTED）。"""
        from app.rag.schema import build_index_params

        mock_client = MagicMock()
        mock_index_params = MagicMock()
        mock_client.prepare_index_params.return_value = mock_index_params

        build_index_params(mock_client)

        # 基础只有 2 个索引
        assert mock_index_params.add_index.call_count == 2


# ════════════════════════════════════════════════════════════════
# 8. models/__init__.py 注册校验
# ════════════════════════════════════════════════════════════════


class TestModelsRegistration:
    """确认新模型已在 __init__.py 中注册。"""

    def test_agent_trace_importable(self):
        from app.models import AgentTrace

        assert AgentTrace.__tablename__ == "agent_traces"

    def test_eval_task_importable(self):
        from app.models import EvalTask

        assert EvalTask.__tablename__ == "eval_tasks"

    def test_all_exports_include_new_models(self):
        from app.models import __all__

        assert "AgentTrace" in __all__
        assert "EvalTask" in __all__
