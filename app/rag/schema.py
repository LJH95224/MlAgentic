"""Milvus Collection Schema 定义（严格对齐 PRD §4.3）。

为什么单独成文件？
- Schema 定义在客户端初始化、入库脚本、单测三处都要复用，集中维护避免漂移。
- 字段维度、capacity 这类常量改起来必须同步动 Schema —— 放在一起降低误改风险。

依赖：pymilvus>=2.6.0
"""

from __future__ import annotations

from pymilvus import CollectionSchema, DataType, FieldSchema, MilvusClient

# 与 PRD §4.3 一一对应的字段配置常量。
# 任何 capacity / max_length 变更都要同步：
#   1) 修改这里
#   2) 修改 docs/progress.md 3.5 节中的字段表
#   3) 跑 scripts/rag_smoke.py 验证

# 向量维度。和 settings.embedding_dimension 必须严格一致 —— 通过传参显式传入，
# 不在此处读 settings，避免单测时强制依赖 .env。
_DEFAULT_DIM = 4096

# 文档/切片相关
_MAX_DOC_ID_LEN = 64
_MAX_CONTENT_LEN = 65535

# 权限：单角色字符串最长 32，最多 20 个角色
_MAX_ROLES = 20
_MAX_ROLE_LEN = 32

# 实体标签：单标签最长 64，最多 50 个
_MAX_ENTITY_TAGS = 50
_MAX_ENTITY_TAG_LEN = 64


def build_knowledge_chunks_schema(dim: int = _DEFAULT_DIM) -> CollectionSchema:
    """构建 knowledge_chunks Collection 的 Schema。

    Args:
        dim: 向量维度，必须与 Embedding 模型输出维度一致。

    Returns:
        CollectionSchema 对象，可直接传给 MilvusClient.create_collection。
    """
    fields = [
        # 主键：切片全局唯一 ID，由入库流程自行分配（INT64，不走 auto_id）。
        # 入库脚本目前用 hash(document_id + chunk_index) 取低 63 位生成。
        FieldSchema(
            name="chunk_id",
            dtype=DataType.INT64,
            is_primary=True,
            auto_id=False,
            description="切片全局唯一标识",
        ),
        # 嵌入向量：HNSW + COSINE
        FieldSchema(
            name="vector",
            dtype=DataType.FLOAT_VECTOR,
            dim=dim,
            description=f"嵌入向量（{dim} 维）",
        ),
        # 文档锚点：与 Neo4j 节点的 document_id 属性对齐（图谱锚点）
        FieldSchema(
            name="document_id",
            dtype=DataType.VARCHAR,
            max_length=_MAX_DOC_ID_LEN,
            description="原文档唯一标识（与 Neo4j Document 节点对齐）",
        ),
        # 切片原文
        FieldSchema(
            name="content",
            dtype=DataType.VARCHAR,
            max_length=_MAX_CONTENT_LEN,
            description="切片文本原文",
        ),
        # 权限标识列表（RAG-04）：检索时叠加 ARRAY_CONTAINS 过滤
        FieldSchema(
            name="allowed_roles",
            dtype=DataType.ARRAY,
            element_type=DataType.VARCHAR,
            max_capacity=_MAX_ROLES,
            max_length=_MAX_ROLE_LEN,
            description="允许访问的角色列表（V1.0 暂存 ['ALL']）",
        ),
        # 实体标签列表（RAG-05）：与 Neo4j Entity 节点的 name 对齐
        FieldSchema(
            name="entity_tags",
            dtype=DataType.ARRAY,
            element_type=DataType.VARCHAR,
            max_capacity=_MAX_ENTITY_TAGS,
            max_length=_MAX_ENTITY_TAG_LEN,
            description="实体标签列表（与 Neo4j Entity.name 对齐，3.6 阶段写入）",
        ),
        # 动态元数据：文档类型 / 来源 / 入库时间等
        FieldSchema(
            name="metadata",
            dtype=DataType.JSON,
            description="动态元数据（type / source / ingested_at 等）",
        ),
    ]

    return CollectionSchema(
        fields=fields,
        description="RAG 知识切片库（PRD §4.3）",
        # enable_dynamic_field=False：禁止运行时新增字段，保持 Schema 显式约束。
        # 元数据扩展走 metadata JSON 字段，而不是动态字段。
        enable_dynamic_field=False,
    )


def build_index_params(client: MilvusClient) -> "object":
    """构建索引参数：向量字段用 HNSW，document_id 用 INVERTED 加速标量过滤。

    Returns:
        IndexParams 对象（pymilvus 内部类型），直接喂给 create_collection。
    """
    index_params = client.prepare_index_params()

    # 向量索引：HNSW + COSINE
    # M / efConstruction 取保守生产参数（中等召回率 + 适中内存）
    index_params.add_index(
        field_name="vector",
        index_type="HNSW",
        index_name="vec_hnsw",
        metric_type="COSINE",
        params={"M": 16, "efConstruction": 200},
    )

    # 标量索引：document_id 上加 INVERTED，加速按文档定位的过滤
    # （allowed_roles / entity_tags 的 ARRAY 过滤 Milvus 2.6 暂无强制索引要求）
    index_params.add_index(
        field_name="document_id",
        index_type="INVERTED",
        index_name="doc_id_inv",
    )

    return index_params


__all__ = [
    "build_knowledge_chunks_schema",
    "build_index_params",
]
