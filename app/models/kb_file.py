"""kb_files 表：文件元数据与异步入库状态（V1.5 PRD §5.3 + V2.0 扩展）。

文件 `id` 同时作为 `document_id` 写入 Milvus 与 Neo4j，方便跨库追溯。

V2.0 新增字段（T0.2）：
- doc_metadata: JSONB — 文档级元数据（IDP-05 提取的标题/作者/日期等）
- summary_brief: Text — 文档摘要（IDP-04 生成的简要摘要）
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


# 文件入库状态枚举（PRD FILE-02）
FILE_STATUS_PENDING = "pending"
FILE_STATUS_PROCESSING = "processing"
FILE_STATUS_COMPLETED = "completed"
FILE_STATUS_FAILED = "failed"
FILE_STATUS_CHOICES = (
    FILE_STATUS_PENDING,
    FILE_STATUS_PROCESSING,
    FILE_STATUS_COMPLETED,
    FILE_STATUS_FAILED,
)


class KbFile(Base):
    """知识库文件表（PRD §5.3）。

    `id` 由应用层生成（UUID），上传时同步写入文件磁盘路径与 Celery 任务 ID。
    progress 各阶段值见 PRD FILE-03 表。
    """

    __tablename__ = "kb_files"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="文件唯一标识；同时作为 document_id 写入 Milvus / Neo4j",
    )

    kb_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属知识库",
    )

    filename: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="用户上传时的原始文件名（含扩展名）",
    )

    file_path: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        comment="服务器磁盘存储路径，形如 {UPLOAD_DIR}/{kb_id}/{file_id}/{filename}",
    )

    file_size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="文件大小（字节）",
    )

    mime_type: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="MIME 类型，决定走哪个解析器",
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=FILE_STATUS_PENDING,
        server_default=FILE_STATUS_PENDING,
        index=True,
        comment="状态：pending / processing / completed / failed",
    )

    progress: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="入库进度 0~100（PRD FILE-03 各阶段值）",
    )

    chunk_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="成功写入 Milvus 的切片数",
    )

    entity_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="成功写入 Neo4j 的实体数（NER 软失败时保持当前值）",
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        comment="failed 时的错误摘要（含简短堆栈）",
    )

    celery_task_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        default=None,
        comment="Celery 任务 ID；删除/重新入库时用它 revoke",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="上传时间",
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="入库完成时间（status=completed 时写入）",
    )

    # ── V2.0 新增字段（T0.2） ──

    doc_metadata: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        default=None,
        comment="V2.0 文档级元数据（IDP-05 提取的标题/作者/日期/来源等）",
    )

    summary_brief: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        comment="V2.0 文档简要摘要（IDP-04 生成，用于双层索引的文档级检索）",
    )

    # 关联反查（KnowledgeBase 那一侧不显式声明 relationship，避免循环 import 风险）
    knowledge_base = relationship(
        "KnowledgeBase",
        foreign_keys=[kb_id],
        lazy="raise",  # 强制显式 selectinload；防 N+1
    )

    def __repr__(self) -> str:
        return f"<KbFile id={self.id} kb={self.kb_id} status={self.status}>"
