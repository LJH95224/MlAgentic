"""SQLAlchemy ORM 模型。

V1.0 包含三张核心表：
- ChatSession    → chat_sessions      会话
- ChatMessage    → chat_messages      消息上下文
- KnowledgeChunk → knowledge_chunks   RAG 知识切片（3.5 阶段启用含 pgvector 的完整定义）
"""

from app.models.session import ChatSession
from app.models.message import ChatMessage

# knowledge_chunks 模型在此导出但当前不做要求（3.5 阶段启用）
# from app.models.knowledge import KnowledgeChunk

__all__ = [
    "ChatSession",
    "ChatMessage",
    # "KnowledgeChunk",
]