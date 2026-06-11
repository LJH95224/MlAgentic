"""KB 文件元数据相关的 Pydantic Schema（V1.5 PRD §3.3 FILE-01~05）。

字段约束严格对齐 PRD §5.3 kb_files 表 + FILE-02/03 列表 / 详情字段。
请求体只有"无"（multipart/form-data 直接接 UploadFile）；响应体覆盖 5 个 endpoint。
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.kb_file import FILE_STATUS_CHOICES


FileStatus = Literal["pending", "processing", "completed", "failed"]


# ──────────────── 通用响应字段 ────────────────


class FileListItem(BaseModel):
    """文件列表项（FILE-02）。

    列表场景不返回 error_message / celery_task_id（详情接口才给），避免列表
    体积过大；前端要查错走 FILE-03 详情。
    """

    id: uuid.UUID
    filename: str
    file_size: int = Field(..., description="文件大小（字节）")
    mime_type: str
    status: FileStatus
    progress: int = Field(..., ge=0, le=100)
    chunk_count: int
    created_at: datetime
    completed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class FileDetail(BaseModel):
    """文件详情 + 进度（FILE-03）。

    比 FileListItem 多三个字段：entity_count / error_message / celery_task_id。
    前端按 2s 轮询此接口观测入库进度，看到 progress=100 且 status=completed 即停。
    """

    id: uuid.UUID
    kb_id: uuid.UUID
    filename: str
    file_size: int
    mime_type: str
    status: FileStatus
    progress: int = Field(..., ge=0, le=100)
    chunk_count: int
    entity_count: int
    error_message: str | None = None
    celery_task_id: str | None = None
    created_at: datetime
    completed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class FileListResponse(BaseModel):
    """文件列表分页响应（FILE-02）。"""

    items: list[FileListItem]
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)
    total: int = Field(..., ge=0)


# ──────────────── 校验工具（schema 同 module 暴露） ────────────────


def is_valid_status(s: str) -> bool:
    """运行期校验 status 字段；ORM 写入前的兜底防御。"""
    return s in FILE_STATUS_CHOICES
