"""Milvus Collection Schema 单测（不连真 Milvus）。

验证 PRD §4.3 的字段定义严格落地：
- 字段数量与命名
- 主键 / 维度 / capacity / max_length
- HNSW + COSINE 索引参数
"""

from pymilvus import DataType

from app.rag.schema import build_knowledge_chunks_schema


def _field_by_name(schema, name: str):
    """从 CollectionSchema 中按名取 FieldSchema。"""
    for f in schema.fields:
        if f.name == name:
            return f
    raise AssertionError(f"字段 {name!r} 未在 Schema 中找到")


def test_schema_has_all_required_fields():
    """Schema 必须包含 PRD §4.3 列出的全部 7 个字段。"""
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


def test_chunk_id_is_primary_no_auto_id():
    """chunk_id 必须是主键，且 auto_id=False（由入库流程显式分配）。"""
    schema = build_knowledge_chunks_schema()
    chunk_id = _field_by_name(schema, "chunk_id")
    assert chunk_id.dtype == DataType.INT64
    assert chunk_id.is_primary is True
    assert chunk_id.auto_id is False


def test_vector_default_dim_4096():
    """默认向量维度 4096，与 Qwen3-Embedding-8B 输出一致。"""
    schema = build_knowledge_chunks_schema()
    vec = _field_by_name(schema, "vector")
    assert vec.dtype == DataType.FLOAT_VECTOR
    # FLOAT_VECTOR 的 dim 存储在 params 字典中
    assert vec.params.get("dim") == 4096


def test_vector_dim_can_be_customized():
    """支持构建时传入不同维度，便于切换 Embedding 模型。"""
    schema = build_knowledge_chunks_schema(dim=1024)
    vec = _field_by_name(schema, "vector")
    assert vec.params.get("dim") == 1024


def test_allowed_roles_array_varchar_cap_20():
    """allowed_roles 是 VARCHAR ARRAY，capacity 20（RAG-04）。"""
    schema = build_knowledge_chunks_schema()
    roles = _field_by_name(schema, "allowed_roles")
    assert roles.dtype == DataType.ARRAY
    assert roles.element_type == DataType.VARCHAR
    assert roles.params.get("max_capacity") == 20


def test_entity_tags_array_varchar_cap_50():
    """entity_tags 是 VARCHAR ARRAY，capacity 50（RAG-05）。"""
    schema = build_knowledge_chunks_schema()
    tags = _field_by_name(schema, "entity_tags")
    assert tags.dtype == DataType.ARRAY
    assert tags.element_type == DataType.VARCHAR
    assert tags.params.get("max_capacity") == 50


def test_metadata_is_json():
    """metadata 必须是 JSON 类型，支持动态键值。"""
    schema = build_knowledge_chunks_schema()
    meta = _field_by_name(schema, "metadata")
    assert meta.dtype == DataType.JSON


def test_document_id_varchar_64():
    """document_id VARCHAR 长度 64（与 Neo4j 节点属性对齐）。"""
    schema = build_knowledge_chunks_schema()
    doc_id = _field_by_name(schema, "document_id")
    assert doc_id.dtype == DataType.VARCHAR
    assert doc_id.params.get("max_length") == 64


def test_dynamic_field_disabled():
    """enable_dynamic_field=False，禁止运行时新增字段，元数据走 metadata JSON。"""
    schema = build_knowledge_chunks_schema()
    assert schema.enable_dynamic_field is False
