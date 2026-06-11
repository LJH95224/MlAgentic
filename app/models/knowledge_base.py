"""knowledge_bases 表：知识库元数据（V1.5 PRD §5.2）。

每个知识库对应：
- 一张 Milvus Collection（命名 `kb_{kb_id_no_hyphen}`，详见 app/rag/naming.py）
- 一组 Neo4j 节点（按 `kb_id` 属性隔离子图）

字段全部按 PRD §5.2 落地。`embedding_dim / chunk_size / chunk_overlap` 创建后只读
（KB-04 明确要求）。
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDMixin


# KB status 枚举（应用层约束；PG 层不建 enum 类型，便于后续扩展）
KB_STATUS_ACTIVE = "active"
KB_STATUS_BUILDING = "building"
KB_STATUS_ERROR = "error"
KB_STATUS_CHOICES = (KB_STATUS_ACTIVE, KB_STATUS_BUILDING, KB_STATUS_ERROR)


class KnowledgeBase(UUIDMixin, Base):
    """知识库表（PRD §5.2）。

    冗余字段 `file_count` / `chunk_count` 在文件入库 / 删除时维护，
    避免列表查询时实时统计（PRD KB-02 明确要求）。
    """

    __tablename__ = "knowledge_bases"

    name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        unique=True,
        index=True,
        comment="知识库名称，全局唯一，最长 128（PRD KB-01）",
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        comment="知识库描述，最长 500（PRD KB-01）",
    )

    embedding_dim: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=4096,
        server_default="4096",
        comment="向量维度，创建后不可修改（PRD KB-04）",
    )

    chunk_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=512,
        server_default="512",
        comment="文本切片大小（Token 数），范围 128~2048",
    )

    chunk_overlap: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=64,
        server_default="64",
        comment="切片重叠大小（Token 数），不超过 chunk_size 的 50%",
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=KB_STATUS_ACTIVE,
        server_default=KB_STATUS_ACTIVE,
        comment="状态：active / building / error",
    )

    file_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="冗余统计：关联文件数（上传成功即 +1）",
    )

    chunk_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="冗余统计：Milvus 向量切片数（入库完成时 += 该文件 chunk 数）",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )

    __table_args__ = (
        # 业务层有更严的校验（chunk_overlap < chunk_size/2），这里只兜底防极端值
        CheckConstraint("chunk_size BETWEEN 128 AND 2048", name="ck_kb_chunk_size_range"),
        CheckConstraint("chunk_overlap >= 0", name="ck_kb_chunk_overlap_nonneg"),
        CheckConstraint("embedding_dim > 0", name="ck_kb_embedding_dim_positive"),
    )

    def __repr__(self) -> str:
        return f"<KnowledgeBase id={self.id} name={self.name!r}>"
