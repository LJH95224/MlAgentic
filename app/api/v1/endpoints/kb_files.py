"""KB 文件管理端点（V1.5 PRD §3.3 FILE-01~05）。

挂在 /api/v1/knowledge-bases/{kb_id}/files 子路径下；所有响应包成 ApiResponse[T]。
"""

import logging
import uuid
from http import HTTPStatus

from fastapi import APIRouter, File, Query, UploadFile

from app.api.deps import DBSessionDep
from app.schemas.kb_file import (
    FileDetail,
    FileListItem,
    FileListResponse,
)
from app.schemas.response import ApiResponse
from app.services import kb_file_service

logger = logging.getLogger(__name__)

# 注意：路径里有 {kb_id}，所以前缀放到 router 顶层
router = APIRouter(prefix="/knowledge-bases/{kb_id}/files", tags=["知识库文件"])


# ──────────────── FILE-01 上传 ────────────────


@router.post(
    "",
    response_model=ApiResponse[FileDetail],
    status_code=HTTPStatus.CREATED,
)
async def upload_kb_file(
    kb_id: uuid.UUID,
    db: DBSessionDep,
    file: UploadFile = File(..., description="上传的文件；multipart/form-data"),
) -> ApiResponse[FileDetail]:
    """上传文件并触发异步入库（FILE-01）。

    返回 file_id 与初始状态（status=pending, progress=0），不等入库完成。
    前端按 2s 间隔轮询 GET /knowledge-bases/{kb_id}/files/{file_id} 观察进度。
    """
    # UploadFile.file 是 SpooledTemporaryFile，可直接 read（边读边量）
    kb_file = await kb_file_service.upload_file(
        db,
        kb_id,
        src_stream=file.file,
        filename=file.filename or "unnamed",
        declared_mime=file.content_type,
    )
    logger.info(
        "FILE-01 endpoint 已接收 file_id=%s kb_id=%s filename=%r",
        kb_file.id,
        kb_id,
        kb_file.filename,
    )
    return ApiResponse[FileDetail].success(
        FileDetail.model_validate(kb_file)
    )


# ──────────────── FILE-02 列表 ────────────────


@router.get(
    "",
    response_model=ApiResponse[FileListResponse],
)
async def list_files(
    kb_id: uuid.UUID,
    db: DBSessionDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> ApiResponse[FileListResponse]:
    """分页查询 KB 文件列表（FILE-02），按 created_at 倒序。"""
    items, total = await kb_file_service.list_kb_files(
        db, kb_id, page=page, page_size=page_size
    )
    payload = FileListResponse(
        items=[FileListItem.model_validate(f) for f in items],
        page=page,
        page_size=page_size,
        total=total,
    )
    return ApiResponse[FileListResponse].success(payload)


# ──────────────── FILE-03 详情 + 进度 ────────────────


@router.get(
    "/{file_id}",
    response_model=ApiResponse[FileDetail],
)
async def get_file_detail(
    kb_id: uuid.UUID,
    file_id: uuid.UUID,
    db: DBSessionDep,
) -> ApiResponse[FileDetail]:
    """查询文件详情与入库进度（FILE-03）。前端按 2s 轮询此接口。"""
    kb_file = await kb_file_service.get_file_or_raise(db, kb_id, file_id)
    return ApiResponse[FileDetail].success(FileDetail.model_validate(kb_file))


# ──────────────── FILE-04 删除 ────────────────


@router.delete(
    "/{file_id}",
    response_model=ApiResponse[None],
)
async def delete_file(
    kb_id: uuid.UUID,
    file_id: uuid.UUID,
    db: DBSessionDep,
) -> ApiResponse[None]:
    """删除文件及其全部资源（FILE-04）。

    清理顺序：revoke Celery → Milvus → Neo4j → PG → 磁盘。
    S3.1 阶段 Milvus / Neo4j 步骤为 stub，S3.2 / S5 接通后自动生效。
    """
    await kb_file_service.delete_file(db, kb_id, file_id)
    logger.info("FILE-04 文件已删除 kb_id=%s file_id=%s", kb_id, file_id)
    return ApiResponse[None].success(None, message="文件已删除")


# ──────────────── FILE-05 重新入库 ────────────────


@router.post(
    "/{file_id}/reindex",
    response_model=ApiResponse[FileDetail],
)
async def reindex_file(
    kb_id: uuid.UUID,
    file_id: uuid.UUID,
    db: DBSessionDep,
) -> ApiResponse[FileDetail]:
    """重新入库（FILE-05）：清旧切片 + 状态重置 + 触发新任务。

    磁盘文件不存在 → 404 提示重新上传。
    """
    kb_file = await kb_file_service.reindex_file(db, kb_id, file_id)
    logger.info(
        "FILE-05 重建任务已触发 kb_id=%s file_id=%s task_id=%s",
        kb_id,
        file_id,
        kb_file.celery_task_id,
    )
    return ApiResponse[FileDetail].success(
        FileDetail.model_validate(kb_file), message="重建任务已提交"
    )
