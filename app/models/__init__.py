"""SQLAlchemy ORM 模型。

V1.0 PostgreSQL 仅保留会话与消息两张表：
- ChatSession    → chat_sessions      会话
- ChatMessage    → chat_messages      消息上下文

按新版 PRD：知识切片库（knowledge_chunks）迁移至 Milvus 管理（详见 §3.5），
不再在 PostgreSQL 中建表，因此此处不导出 KnowledgeChunk。
"""

from app.models.session import ChatSession
from app.models.message import ChatMessage

__all__ = [
    "ChatSession",
    "ChatMessage",
]
