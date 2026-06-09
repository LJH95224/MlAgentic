"""knowledge_chunks 表：RAG 知识切片（3.5 阶段启用）。

当前仅定义模型，不参与 create_all。3.5 阶段改由 alembic 迁移创建。
"""

import uuid

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# --------------------
# pgvector 类型（懒导入）
# pgvector 的 Vector 类型在 import 时要求已安装 pgvector 扩展。
# 为避免模型层加载失败，延后到真正使用时再引用。
# --------------------
_Vector = None


def _get_vector_type():
    global _Vector
    if _Vector is None:
        try:
            from pgvector.sqlalchemy import Vector  # noqa: F811

            _Vector = Vector
        except ImportError:
            raise ImportError(
                "pgvector 未安装或数据库未启用 vector 扩展。"
                "请执行: pip install pgvector 并在 psql 中执行 CREATE EXTENSION IF NOT EXISTS vector;"
            )
    return _Vector


class KnowledgeChunk(Base):
    """知识切片表（PRD §4.3）。

    embedding 列使用 pgvector 的 Vector 类型，维度在编译期指定。
    注意：若更换 Embedding 模型（例如从 text-embedding-3-small 换成 multilingual-e5），
    需要在创建该模型实例时同步调整维度参数。
    """

    __tablename__ = "knowledge_chunks"

    VECTOR_DIMENSION = 1536  # 默认使用 text-embedding-3-small 的维度

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="切片唯一标识",
    )

    document_id: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="原文档标识"
    )

    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="切片文本内容"
    )

    # embedding 列：依赖 pgvector，采用延后初始化模式。
    # 使用方法：
    #   from app.models.knowledge import KnowledgeChunk
    #   VectorType = KnowledgeChunk.get_vector_type()
    #   KnowledgeChunk.__table__.c.embedding.type = VectorType(1536)
    embedding: Mapped["_Vector | None"] = mapped_column(  # type: ignore[valid-type]
        "embedding",
        # 使用 Text 占位，避免在 pgvector 未就绪时 Schema 崩溃
        Text,
        nullable=True,
        comment="向量表示（初始为 Text 占位，需替换为 Vector(N)）",
    )

    chunk_metadata: Mapped[dict | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
        default=None,
        comment="元数据（作者、时间、类型等）",
    )

    def __repr__(self) -> str:
        return f"<KnowledgeChunk id={self.id} doc={self.document_id}>"

    @classmethod
    def get_vector_type(cls, dimension: int | None = None):
        """获取 pgvector 的 Vector 类型。

        在首次调用此方法之前，pgvector 不会被导入。
        仅在 3.5 阶段真正启用 RAG 时才会调用。
        """
        vt = _get_vector_type()
        dim = dimension or cls.VECTOR_DIMENSION
        return vt(dim)



