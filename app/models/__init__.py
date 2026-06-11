"""SQLAlchemy ORM 模型。

V1.0：会话 + 消息
V1.5：扩展会话字段（标题/摘要/计数）+ 新增知识库 + 文件元数据
"""

from app.models.kb_file import KbFile
from app.models.knowledge_base import KnowledgeBase
from app.models.message import ChatMessage
from app.models.session import ChatSession

__all__ = [
    "ChatSession",
    "ChatMessage",
    "KnowledgeBase",
    "KbFile",
]
