"""Agentic RAG 模块（3.5 阶段引入，基于 Milvus 分布式向量库）。

按新版 PRD：
- 通过 PyMilvus 接入 Milvus 服务（连接配置写在 .env）
- Collection: knowledge_chunks，HNSW + COSINE，4096 维向量
- 暴露 `search_knowledge_base(query, top_k, **kwargs)` 工具供 Agent 调用
- 支持标量过滤、allowed_roles 权限过滤、entity_tags 图谱锚点

对外入口：
- init_milvus() / close_milvus()：lifespan 接入
- get_milvus_client()：业务代码获取单例
- aembed_texts()：文本批量转向量
- search_knowledge_base：注册到 Agent 工具集的 LangChain @tool
"""

from app.rag.embedding import aembed_texts
from app.rag.milvus_client import close_milvus, get_milvus_client, init_milvus
from app.rag.retriever import search_knowledge_base

__all__ = [
    "init_milvus",
    "get_milvus_client",
    "close_milvus",
    "aembed_texts",
    "search_knowledge_base",
]
