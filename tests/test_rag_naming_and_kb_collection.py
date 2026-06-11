"""V1.5 S2.0 RAG 基础设施单测（命名 + Schema 扩展 + KB Collection 生命周期）。

覆盖：
- naming.build_kb_collection_name：UUID 对象 / 字符串 / 非法输入
- naming.is_kb_collection_name：识别 KB 命名 vs 其它 collection
- schema.build_kb_collection_schema：在 V1.0 schema 基础上多 1 个 kb_id 字段
- milvus_client.create_kb_collection / drop_kb_collection：mock Milvus client
  覆盖：新建分支、已存在分支、创建失败回滚、drop 不存在的、drop 真删
"""

import uuid
from unittest.mock import MagicMock

import pytest

from app.rag.naming import (
    KB_COLLECTION_PREFIX,
    build_kb_collection_name,
    is_kb_collection_name,
)
from app.rag.schema import (
    build_kb_collection_schema,
    build_knowledge_chunks_schema,
)


# ──────────────── naming ────────────────


def test_build_kb_collection_name_from_uuid():
    kb_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
    assert build_kb_collection_name(kb_id) == "kb_a1b2c3d4e5f67890abcdef1234567890"


def test_build_kb_collection_name_from_string():
    name = build_kb_collection_name("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
    assert name == "kb_a1b2c3d4e5f67890abcdef1234567890"


def test_build_kb_collection_name_starts_with_letter():
    """Milvus Collection 名必须以字母开头。"""
    for _ in range(20):
        name = build_kb_collection_name(uuid.uuid4())
        assert name[0].isalpha()


def test_build_kb_collection_name_length_safe():
    """生成的名长度（3+32=35）远小于 Milvus 255 上限。"""
    name = build_kb_collection_name(uuid.uuid4())
    assert len(name) == len(KB_COLLECTION_PREFIX) + 32
    assert len(name) < 255


def test_build_kb_collection_name_chars_safe():
    """生成的名只含字母、数字、下划线（Milvus 命名约束）。"""
    name = build_kb_collection_name(uuid.uuid4())
    assert all(c.isalnum() or c == "_" for c in name)


def test_build_kb_collection_name_rejects_invalid_uuid():
    with pytest.raises(ValueError, match="不是合法 UUID"):
        build_kb_collection_name("not-a-uuid")


def test_is_kb_collection_name_positive():
    name = build_kb_collection_name(uuid.uuid4())
    assert is_kb_collection_name(name) is True


def test_is_kb_collection_name_negative_v1_collection():
    """V1.0 的 knowledge_chunks 不应被识别成 KB Collection。"""
    assert is_kb_collection_name("knowledge_chunks") is False


def test_is_kb_collection_name_negative_bad_suffix():
    assert is_kb_collection_name("kb_not32hex") is False
    assert is_kb_collection_name("kb_XXXXXXXX-XXXX") is False
    assert is_kb_collection_name("kb_") is False
    assert is_kb_collection_name("kb_a1b2c3d4e5f67890abcdef12345678901") is False  # 33 hex


# ──────────────── Schema 扩展 ────────────────


def test_kb_schema_has_exactly_one_more_field_than_v1():
    """V1.5 KB Schema = V1.0 Schema + 1 个 kb_id 字段。"""
    v1 = build_knowledge_chunks_schema()
    v1_5 = build_kb_collection_schema()
    assert len(v1_5.fields) == len(v1.fields) + 1


def test_kb_schema_has_kb_id_field():
    schema = build_kb_collection_schema()
    field_names = {f.name for f in schema.fields}
    assert "kb_id" in field_names


def test_kb_schema_kb_id_field_is_varchar_64():
    schema = build_kb_collection_schema()
    kb_id_field = next(f for f in schema.fields if f.name == "kb_id")
    from pymilvus import DataType

    assert kb_id_field.dtype == DataType.VARCHAR
    # params 里存 max_length
    assert kb_id_field.params.get("max_length") == 64


def test_kb_schema_preserves_v1_fields():
    """V1.0 的 7 个字段都必须保留（chunk_id / vector / document_id / content /
    allowed_roles / entity_tags / metadata），权限/图谱基础不能丢。"""
    schema = build_kb_collection_schema()
    field_names = {f.name for f in schema.fields}
    for expected in (
        "chunk_id",
        "vector",
        "document_id",
        "content",
        "allowed_roles",
        "entity_tags",
        "metadata",
    ):
        assert expected in field_names, f"V1.5 KB Schema 丢失 V1.0 字段: {expected}"


def test_kb_schema_disables_dynamic_field():
    """Schema 显式约束，不允许运行时新增字段。"""
    schema = build_kb_collection_schema()
    assert schema.enable_dynamic_field is False


def test_kb_schema_vector_dim_param():
    schema = build_kb_collection_schema(dim=1024)
    vec = next(f for f in schema.fields if f.name == "vector")
    assert vec.params.get("dim") == 1024


# ──────────────── milvus_client.create_kb_collection / drop ────────────────


@pytest.fixture
def fake_milvus_client(monkeypatch):
    """替换 milvus_client._client 为一个 MagicMock。"""
    import app.rag.milvus_client as mod

    fake = MagicMock()
    fake.has_collection.return_value = False
    fake.prepare_index_params.return_value = MagicMock()
    monkeypatch.setattr(mod, "_client", fake)
    yield fake
    monkeypatch.setattr(mod, "_client", None)


def test_create_kb_collection_when_absent(fake_milvus_client):
    from app.rag.milvus_client import create_kb_collection

    fake_milvus_client.has_collection.return_value = False
    kb_id = uuid.uuid4()
    name = create_kb_collection(kb_id, dim=4096)

    assert name == build_kb_collection_name(kb_id)
    fake_milvus_client.create_collection.assert_called_once()
    # 用 collection_name 关键字调用
    call_kwargs = fake_milvus_client.create_collection.call_args.kwargs
    assert call_kwargs["collection_name"] == name
    # 创建后必须 load
    fake_milvus_client.load_collection.assert_called_once_with(name)


def test_create_kb_collection_when_exists_skips_creation(fake_milvus_client):
    """已存在 → 跳过 create，但仍 load（幂等）。"""
    from app.rag.milvus_client import create_kb_collection

    fake_milvus_client.has_collection.return_value = True
    kb_id = uuid.uuid4()
    name = create_kb_collection(kb_id)

    fake_milvus_client.create_collection.assert_not_called()
    fake_milvus_client.load_collection.assert_called_once_with(name)


def test_create_kb_collection_raises_runtime_on_failure(fake_milvus_client):
    """create_collection 抛任何异常 → 包装成 RuntimeError 让上层回滚 PG。"""
    from app.rag.milvus_client import create_kb_collection

    fake_milvus_client.has_collection.return_value = False
    fake_milvus_client.create_collection.side_effect = Exception("milvus down")

    with pytest.raises(RuntimeError, match="创建 KB Collection 失败"):
        create_kb_collection(uuid.uuid4())


def test_create_kb_collection_uses_settings_dim_when_not_specified(
    fake_milvus_client, monkeypatch
):
    """不传 dim → 用 settings.embedding_dimension。"""
    from app.rag.milvus_client import create_kb_collection

    fake_milvus_client.has_collection.return_value = False
    monkeypatch.setenv("EMBEDDING_DIMENSION", "1024")
    from app.core.config import get_settings

    get_settings.cache_clear()

    create_kb_collection(uuid.uuid4())  # 不传 dim

    # 直接验 create_collection 被调一次即可（dim 进入 schema 不易反查；
    # 关键的 dim 校验在 test_kb_schema_vector_dim_param 已覆盖）
    fake_milvus_client.create_collection.assert_called_once()


def test_drop_kb_collection_when_exists_returns_true(fake_milvus_client):
    from app.rag.milvus_client import drop_kb_collection

    fake_milvus_client.has_collection.return_value = True
    kb_id = uuid.uuid4()
    result = drop_kb_collection(kb_id)
    assert result is True
    fake_milvus_client.release_collection.assert_called_once()
    fake_milvus_client.drop_collection.assert_called_once()


def test_drop_kb_collection_when_absent_returns_false(fake_milvus_client):
    """不存在 → 返回 False，不抛错；幂等。"""
    from app.rag.milvus_client import drop_kb_collection

    fake_milvus_client.has_collection.return_value = False
    result = drop_kb_collection(uuid.uuid4())
    assert result is False
    fake_milvus_client.drop_collection.assert_not_called()


def test_drop_kb_collection_release_failure_still_drops(fake_milvus_client):
    """release 失败不阻断 drop（pymilvus drop 内部会再 release 一次）。"""
    from app.rag.milvus_client import drop_kb_collection

    fake_milvus_client.has_collection.return_value = True
    fake_milvus_client.release_collection.side_effect = Exception("release error")

    result = drop_kb_collection(uuid.uuid4())
    assert result is True  # drop 仍成功
    fake_milvus_client.drop_collection.assert_called_once()


def test_drop_kb_collection_drop_failure_raises_runtime(fake_milvus_client):
    """drop 真失败 → RuntimeError 让上层知道清理不彻底。"""
    from app.rag.milvus_client import drop_kb_collection

    fake_milvus_client.has_collection.return_value = True
    fake_milvus_client.drop_collection.side_effect = Exception("drop error")

    with pytest.raises(RuntimeError, match="删除 KB Collection 失败"):
        drop_kb_collection(uuid.uuid4())


def test_kb_collection_exists(fake_milvus_client):
    from app.rag.milvus_client import kb_collection_exists

    fake_milvus_client.has_collection.return_value = True
    assert kb_collection_exists(uuid.uuid4()) is True

    fake_milvus_client.has_collection.return_value = False
    assert kb_collection_exists(uuid.uuid4()) is False


def test_create_kb_collection_raises_when_milvus_not_initialized(monkeypatch):
    """未初始化 Milvus 就调 create_kb_collection → 透传 get_milvus_client 的 RuntimeError。"""
    import app.rag.milvus_client as mod
    from app.rag.milvus_client import create_kb_collection

    monkeypatch.setattr(mod, "_client", None)
    with pytest.raises(RuntimeError, match="尚未初始化"):
        create_kb_collection(uuid.uuid4())
