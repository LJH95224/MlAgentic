"""Agentic RAG 模块（V1.0 §3.5 引入 Milvus；V1.5 §3.2 扩展为多 KB Collection；V2.0 结构感知 + BM25）。

按 V1.5 PRD：
- 通过 PyMilvus 接入 Milvus 服务（连接配置写在 .env）
- V1.0 时期：单 Collection knowledge_chunks（保留供老数据查询）
- V1.5 起：每个知识库独立 Collection `kb_{kb_id_no_hyphen}`，详见 [naming.py](naming.py)
- 暴露 `search_knowledge_base(query, top_k, **kwargs)` 工具供 Agent 调用
- 支持标量过滤、allowed_roles 权限过滤、entity_tags 图谱锚点

V2.0 新增：
- `create_v2_kb_collection(kb_id, dim)`：V2 Schema（含结构感知字段 + 稀疏向量 + BM25 索引）
- V1.5 的 `/api/v1/...` 接口完全不动，继续用 V1.5 Schema
- V2 的 `/api/v2/...` 接口将使用 V2 Schema 创建的 Collection

对外入口：
- init_milvus() / close_milvus()：lifespan 接入
- get_milvus_client()：业务代码获取单例
- aembed_texts()：文本批量转向量
- search_knowledge_base：注册到 Agent 工具集的 LangChain @tool

V1.5 新增（KB Collection 生命周期，供 kb_service 用）：
- create_kb_collection(kb_id, dim)：创建 + 建索引 + load
- drop_kb_collection(kb_id)：release + drop
- kb_collection_exists(kb_id)：健康检查
- build_kb_collection_name(kb_id)：唯一的命名规则真相

V2.0 新增：
- create_v2_kb_collection(kb_id, dim)：V2 Schema + BM25 稀疏向量索引
"""

from app.rag.embedding import aembed_texts
from app.rag.milvus_client import (
    close_milvus,
    create_kb_collection,
    create_v2_kb_collection,
    drop_kb_collection,
    get_milvus_client,
    init_milvus,
    kb_collection_exists,
)
from app.rag.naming import KB_COLLECTION_PREFIX, build_kb_collection_name
from app.rag.retriever import search_knowledge_base

__all__ = [
    "init_milvus",
    "get_milvus_client",
    "close_milvus",
    "aembed_texts",
    "search_knowledge_base",
    # V1.5 多 KB Collection
    "create_kb_collection",
    "drop_kb_collection",
    "kb_collection_exists",
    "build_kb_collection_name",
    "KB_COLLECTION_PREFIX",
    # V2.0
    "create_v2_kb_collection",
]
