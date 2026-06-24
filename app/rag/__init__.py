"""Agentic RAG 模块。

包含：
- 通过 PyMilvus 接入 Milvus 服务（连接配置写在 .env）
- 每个知识库独立 Collection `kb_{kb_id_no_hyphen}`，详见 [naming.py](naming.py)
- 暴露 `search_knowledge_base(query, top_k, **kwargs)` 工具供 Agent 调用
- 支持标量过滤、allowed_roles 权限过滤、entity_tags 图谱锚点
- `create_v2_kb_collection(kb_id, dim)`：含结构感知字段 + 稀疏向量 + BM25 索引

对外入口：
- init_milvus() / close_milvus()：lifespan 接入
- get_milvus_client()：业务代码获取单例
- aembed_texts()：文本批量转向量
- search_knowledge_base：注册到 Agent 工具集的 LangChain @tool

KB Collection 生命周期（供 kb_service 用）：
- create_kb_collection(kb_id, dim)：创建 + 建索引 + load
- drop_kb_collection(kb_id)：release + drop
- kb_collection_exists(kb_id)：健康检查
- build_kb_collection_name(kb_id)：唯一的命名规则真相
- create_v2_kb_collection(kb_id, dim)：新版 Schema + BM25 稀疏向量索引
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
    "create_kb_collection",
    "drop_kb_collection",
    "kb_collection_exists",
    "build_kb_collection_name",
    "KB_COLLECTION_PREFIX",
    "create_v2_kb_collection",
]
