"""知识库管理端点（V1.5 PRD §3.2 KB-01~05）。

所有响应包成 ApiResponse[T]；错误走 BusinessError → 统一 handler 翻译。
"""

import logging
import uuid
from http import HTTPStatus

from fastapi import APIRouter, Query

from app.api.deps import DBSessionDep
from app.schemas.knowledge_base import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseDetail,
    KnowledgeBaseListItem,
    KnowledgeBaseListResponse,
    KnowledgeBaseUpdateRequest,
)
from app.schemas.response import ApiResponse
from app.services import kb_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge-bases", tags=["知识库管理"])


# ──────────────── KB-01 创建 ────────────────


@router.post(
    "",
    response_model=ApiResponse[KnowledgeBaseDetail],
    status_code=HTTPStatus.CREATED,
)
async def create_knowledge_base(
    body: KnowledgeBaseCreateRequest,
    db: DBSessionDep,
) -> ApiResponse[KnowledgeBaseDetail]:
    """创建知识库（KB-01）。

    同步完成 PG 写入 + Milvus Collection 创建；任一失败整体回滚。
    """
    kb = await kb_service.create_kb(
        db,
        name=body.name,
        description=body.description,
        embedding_dim=body.embedding_dim,
        chunk_size=body.chunk_size,
        chunk_overlap=body.chunk_overlap,
    )
    logger.info("知识库已创建: id=%s name=%r", kb.id, kb.name)
    # 刚创建：entity_count = 0
    return ApiResponse[KnowledgeBaseDetail].success(
        KnowledgeBaseDetail.from_orm_kb(kb, entity_count=0)
    )


# ──────────────── KB-02 列表 ────────────────


@router.get(
    "",
    response_model=ApiResponse[KnowledgeBaseListResponse],
)
async def list_knowledge_bases(
    db: DBSessionDep,
    page: int = Query(1, ge=1, description="页码（从 1 起）"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数（1~100）"),
) -> ApiResponse[KnowledgeBaseListResponse]:
    """分页查询知识库列表（KB-02），按 created_at 倒序。"""
    items, total = await kb_service.list_kbs(db, page=page, page_size=page_size)
    payload = KnowledgeBaseListResponse(
        items=[KnowledgeBaseListItem.from_orm_kb(kb) for kb in items],
        page=page,
        page_size=page_size,
        total=total,
    )
    return ApiResponse[KnowledgeBaseListResponse].success(payload)


# ──────────────── KB-03 详情 ────────────────


@router.get(
    "/{kb_id}",
    response_model=ApiResponse[KnowledgeBaseDetail],
)
async def get_knowledge_base_detail(
    kb_id: uuid.UUID, db: DBSessionDep
) -> ApiResponse[KnowledgeBaseDetail]:
    """查询知识库详情（KB-03）。

    `entity_count` 走 kb_service.count_entities_for_kb（S2 阶段 stub 返回 0；
    S5 阶段接通 Neo4j 真实计数）。
    """
    kb = await kb_service.get_kb_or_raise(db, kb_id)
    entity_count = await kb_service.count_entities_for_kb(kb_id)
    return ApiResponse[KnowledgeBaseDetail].success(
        KnowledgeBaseDetail.from_orm_kb(kb, entity_count=entity_count)
    )


# ──────────────── KB-04 更新 ────────────────


@router.patch(
    "/{kb_id}",
    response_model=ApiResponse[KnowledgeBaseDetail],
)
async def update_knowledge_base(
    kb_id: uuid.UUID,
    body: KnowledgeBaseUpdateRequest,
    db: DBSessionDep,
) -> ApiResponse[KnowledgeBaseDetail]:
    """更新知识库 name / description（KB-04）。

    PRD 明确：embedding_dim / chunk_size / chunk_overlap 创建后只读；
    传入会被 Schema 层 extra="forbid" 拦截 → 422。
    """
    # 用 model_fields_set 区分 "未传 description" vs "显式传 description=None（清空）"
    description_was_set = "description" in body.model_fields_set
    kb = await kb_service.update_kb(
        db,
        kb_id,
        name=body.name,
        description=body.description,
        description_was_set=description_was_set,
    )
    logger.info("知识库已更新: id=%s name=%r", kb.id, kb.name)
    # 更新接口不重新查 Neo4j 计数（额外开销不值），返回 0 占位；
    # 前端要看实时 entity_count 调 GET /{kb_id} 详情
    return ApiResponse[KnowledgeBaseDetail].success(
        KnowledgeBaseDetail.from_orm_kb(kb, entity_count=0)
    )


# ──────────────── KB-05 删除 ────────────────


@router.delete(
    "/{kb_id}",
    response_model=ApiResponse[None],
)
async def delete_knowledge_base(
    kb_id: uuid.UUID, db: DBSessionDep
) -> ApiResponse[None]:
    """完全清理知识库及其全部资源（KB-05）。

    严格按 PRD 顺序：Milvus drop → PG delete → Neo4j delete（S5 接通）。
    不可撤销，建议前端二次确认。
    """
    await kb_service.delete_kb(db, kb_id)
    logger.info("知识库已删除: id=%s", kb_id)
    return ApiResponse[None].success(None, message="知识库已删除")
