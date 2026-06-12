"""Milvus Collection Schema 定义（严格对齐 PRD §4.3 + V2.0 扩展）。

为什么单独成文件？
- Schema 定义在客户端初始化、入库脚本、单测三处都要复用，集中维护避免漂移。
- 字段维度、capacity 这类常量改起来必须同步动 Schema —— 放在一起降低误改风险。

V2.0 变更（T0.3）：
- 新增 `build_v2_kb_collection_schema()`：在 V1.5 基础上加 7 个字段
  （heading_path / block_type / page_number / position_index / parent_chunk_id / is_summary / sparse_vector）
- 稀疏向量 `sparse_vector: SPARSE_FLOAT_VECTOR` 用于 BM25 混合检索
- 索引：稠密向量 HNSW+COSINE，稀疏向量 SPARSE_INVERTED_INDEX+BM25

依赖：pymilvus>=2.6.0（Milvus 2.5+ 即支持稀疏向量，当前镜像 v2.6.18）
"""

from __future__ import annotations

from pymilvus import CollectionSchema, DataType, FieldSchema, Function, FunctionType, MilvusClient

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

# ── V2.0 新增字段常量 ──
_MAX_HEADING_PATH_CAPACITY = 10  # 标题层级路径最多 10 级
_MAX_HEADING_PATH_LEN = 256  # 单级标题最长 256 字符
_MAX_BLOCK_TYPE_LEN = 32  # block_type 枚举最长
_MAX_PARENT_CHUNK_ID_LEN = 64  # 父 chunk ID（UUID hex）


def build_knowledge_chunks_schema(dim: int = _DEFAULT_DIM) -> CollectionSchema:
    """构建 knowledge_chunks Collection 的 Schema（V1.0 基线）。

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
    "build_kb_collection_schema",
    "build_index_params",
    # V2.0
    "build_v2_kb_collection_schema",
    "build_v2_index_params",
]


# ──────────────── V1.5 多 KB Collection ────────────────


# KB Collection 比 V1.0 多一个 kb_id 冗余字段（PRD §5.4）
_MAX_KB_ID_LEN = 64


def build_kb_collection_schema(dim: int = _DEFAULT_DIM) -> CollectionSchema:
    """构建 V1.5 KB Collection 的 Schema（在 V1.0 基础上加 kb_id 冗余字段）。

    与 `build_knowledge_chunks_schema` 的差异：
    - 新增 `kb_id` VARCHAR(64) 字段（PRD §5.4）
      虽然每个 KB 一个独立 Collection 已天然隔离了数据，但 Schema 里冗余存一份
      `kb_id` 便于跨 Collection 调试 / 运维查询时定位（比如导出某 KB 全部切片）

    Args:
        dim: 向量维度，必须与 KB 配置的 embedding_dim 严格一致。
    """
    # 复用 V1.0 的 7 个字段
    base_schema = build_knowledge_chunks_schema(dim=dim)
    base_fields = list(base_schema.fields)

    # 追加 V1.5 的 kb_id 字段
    kb_id_field = FieldSchema(
        name="kb_id",
        dtype=DataType.VARCHAR,
        max_length=_MAX_KB_ID_LEN,
        description="所属知识库 UUID（冗余字段，便于跨 Collection 运维查询；PRD §5.4）",
    )

    return CollectionSchema(
        fields=base_fields + [kb_id_field],
        description="V1.5 KB 切片库（PRD §5.4，按 kb_id 独立 Collection）",
        enable_dynamic_field=False,
    )


# ──────────────── V2.0 KB Collection Schema ────────────────


def build_v2_kb_collection_schema(dim: int = _DEFAULT_DIM) -> CollectionSchema:
    """构建 V2.0 KB Collection 的 Schema（15 字段 + BM25 Function）。

    V2.0 不复用 V1.5 base_fields，因为 content 字段需要加 enable_analyzer=True
    以支持 Milvus 内置 BM25 Function 自动从文本生成稀疏向量。

    V2.0 关键变更：
    - content 字段加 enable_analyzer=True（Milvus BM25 Function 必需）
    - 添加 BM25 Function：content → sparse_vector（插入数据时自动计算）
    - sparse_vector 字段仍需显式定义，但插入时不需要手动填写
    - 新增 7 个结构感知字段 + 1 个稀疏向量字段

    Args:
        dim: 稠密向量维度，必须与 KB 配置的 embedding_dim 严格一致。

    Returns:
        CollectionSchema 对象，可直接传给 MilvusClient.create_collection。
    """
    # ── V1.0 基线 7 字段（content 加 enable_analyzer=True） ──
    v2_base_fields = [
        FieldSchema(
            name="chunk_id",
            dtype=DataType.INT64,
            is_primary=True,
            auto_id=False,
            description="切片全局唯一标识",
        ),
        FieldSchema(
            name="vector",
            dtype=DataType.FLOAT_VECTOR,
            dim=dim,
            description=f"嵌入向量（{dim} 维）",
        ),
        FieldSchema(
            name="document_id",
            dtype=DataType.VARCHAR,
            max_length=_MAX_DOC_ID_LEN,
            description="原文档唯一标识（与 Neo4j Document 节点对齐）",
        ),
        # content 字段：enable_analyzer=True 是 Milvus BM25 Function 的必要条件
        # 让 Milvus 在插入时自动对文本做分词，生成 BM25 稀疏向量
        FieldSchema(
            name="content",
            dtype=DataType.VARCHAR,
            max_length=_MAX_CONTENT_LEN,
            enable_analyzer=True,
            description="切片文本原文（enable_analyzer 用于 BM25 Function 自动分词）",
        ),
        FieldSchema(
            name="allowed_roles",
            dtype=DataType.ARRAY,
            element_type=DataType.VARCHAR,
            max_capacity=_MAX_ROLES,
            max_length=_MAX_ROLE_LEN,
            description="允许访问的角色列表",
        ),
        FieldSchema(
            name="entity_tags",
            dtype=DataType.ARRAY,
            element_type=DataType.VARCHAR,
            max_capacity=_MAX_ENTITY_TAGS,
            max_length=_MAX_ENTITY_TAG_LEN,
            description="实体标签列表（与 Neo4j Entity.name 对齐）",
        ),
        FieldSchema(
            name="metadata",
            dtype=DataType.JSON,
            description="动态元数据",
        ),
    ]

    # ── V1.5 追加字段 ──
    v15_fields = [
        FieldSchema(
            name="kb_id",
            dtype=DataType.VARCHAR,
            max_length=_MAX_KB_ID_LEN,
            description="所属知识库 UUID",
        ),
    ]

    # ── V2.0 新增 7 个字段 ──
    v2_fields = [
        FieldSchema(
            name="heading_path",
            dtype=DataType.ARRAY,
            element_type=DataType.VARCHAR,
            max_capacity=_MAX_HEADING_PATH_CAPACITY,
            max_length=_MAX_HEADING_PATH_LEN,
            description="标题层级路径（如 ['第1章', '1.1 节']；IDP-01）",
        ),
        FieldSchema(
            name="block_type",
            dtype=DataType.VARCHAR,
            max_length=_MAX_BLOCK_TYPE_LEN,
            description="块类型：paragraph / heading / table / code / list（IDP-01）",
        ),
        FieldSchema(
            name="page_number",
            dtype=DataType.INT32,
            description="页码（PDF 有效，其他格式 nullable；IDP-01）",
            nullable=True,
        ),
        FieldSchema(
            name="position_index",
            dtype=DataType.INT32,
            description="文档内切片序号（从 0 开始；IDP-02）",
        ),
        FieldSchema(
            name="parent_chunk_id",
            dtype=DataType.VARCHAR,
            max_length=_MAX_PARENT_CHUNK_ID_LEN,
            description="父 chunk ID（双层索引时摘要指向原文；IDP-04）",
            nullable=True,
        ),
        FieldSchema(
            name="is_summary",
            dtype=DataType.BOOL,
            description="是否为摘要 chunk（双层索引时区分摘要与原文；IDP-04）",
        ),
        # sparse_vector：BM25 Function 的输出字段，插入时不需要手动填写
        FieldSchema(
            name="sparse_vector",
            dtype=DataType.SPARSE_FLOAT_VECTOR,
            description="BM25 稀疏向量（由 BM25 Function 自动生成；HRE-03）",
        ),
    ]

    all_fields = v2_base_fields + v15_fields + v2_fields

    # ── BM25 Function：content → sparse_vector ──
    # Milvus 2.5+ 内置 BM25 Function，插入数据时自动从 content 文本生成稀疏向量，
    # 无需手动计算。查询时直接传原始文本做 BM25 检索。
    bm25_function = Function(
        name="bm25_fn",
        input_field_names=["content"],
        output_field_names=["sparse_vector"],
        function_type=FunctionType.BM25,
    )

    return CollectionSchema(
        fields=all_fields,
        functions=[bm25_function],
        description="V2.0 KB 切片库（结构感知切片 + BM25 混合检索；IDP-01/02 + HRE-03）",
        enable_dynamic_field=False,
    )


def build_v2_index_params(client: MilvusClient) -> "object":
    """构建 V2.0 索引参数：在 V1.5 基础上追加稀疏向量 BM25 索引。

    V2.0 索引方案：
    - 稠密向量：HNSW + COSINE（同 V1.5）
    - 稀疏向量：SPARSE_INVERTED_INDEX + BM25（V2.0 新增）
      - bm25_k1=1.2：词频饱和参数（标准值，控制词频增长曲线）
      - bm25_b=0.75：文档长度归一化参数（标准值，惩罚长文档）
      - drop_ratio_build=0.2：建索引时丢弃低频词后 20%，减小体积
    - document_id：INVERTED（同 V1.5）

    Returns:
        IndexParams 对象，直接喂给 create_collection。
    """
    index_params = client.prepare_index_params()

    # 稠密向量索引：HNSW + COSINE
    index_params.add_index(
        field_name="vector",
        index_type="HNSW",
        index_name="vec_hnsw",
        metric_type="COSINE",
        params={"M": 16, "efConstruction": 200},
    )

    # 稀疏向量索引：SPARSE_INVERTED_INDEX + BM25
    # bm25_k1 / bm25_b 是经典 BM25 标准参数；drop_ratio_build 减小索引体积
    index_params.add_index(
        field_name="sparse_vector",
        index_type="SPARSE_INVERTED_INDEX",
        index_name="sparse_bm25",
        metric_type="BM25",
        params={
            "bm25_k1": 1.2,
            "bm25_b": 0.75,
            "drop_ratio_build": 0.2,
        },
    )

    # 标量索引：document_id 上加 INVERTED
    index_params.add_index(
        field_name="document_id",
        index_type="INVERTED",
        index_name="doc_id_inv",
    )

    return index_params
