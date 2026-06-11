"""会话端点（V1.0 API-01 + V1.5 SES-01~06）。

V1.5 起所有响应包成 `ApiResponse[T]`（PRD §7.1）；4xx 错误由 BusinessError 统一翻译。
"""

import logging
import uuid
from http import HTTPStatus

from fastapi import APIRouter, Query

from app.api.deps import DBSessionDep
from app.schemas.message import MessageItem, MessageListResponse
from app.schemas.response import ApiResponse
from app.schemas.session import (
    SessionCreateRequest,
    SessionDetail,
    SessionListItem,
    SessionListResponse,
    SessionUpdateRequest,
)
from app.services import session_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["会话管理"])


# ──────────────── SES-01 创建 ────────────────


@router.post(
    "",
    response_model=ApiResponse[SessionDetail],
    status_code=HTTPStatus.CREATED,
)
async def create_session(
    db: DBSessionDep,
    body: SessionCreateRequest | None = None,
) -> ApiResponse[SessionDetail]:
    """创建一个新的对话会话（V1.0 API-01 / V1.5 SES-01）。

    Body 可选；不传或不带 title → title 字段为 null（待 SES-07 异步任务生成）。
    """
    title = body.title if body else None
    session = await session_service.create_session(db, title=title)
    logger.info("会话已创建: id=%s title=%r", session.id, session.title)
    return ApiResponse[SessionDetail].success(
        SessionDetail.from_orm_session(session)
    )


# ──────────────── SES-02 列表 ────────────────


@router.get(
    "",
    response_model=ApiResponse[SessionListResponse],
)
async def list_sessions(
    db: DBSessionDep,
    page: int = Query(1, ge=1, description="页码（从 1 起）"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数（1~100）"),
) -> ApiResponse[SessionListResponse]:
    """分页查询会话列表（V1.5 SES-02），按 updated_at 倒序。"""
    items, total = await session_service.list_sessions(db, page=page, page_size=page_size)
    payload = SessionListResponse(
        items=[SessionListItem.from_orm_session(s) for s in items],
        page=page,
        page_size=page_size,
        total=total,
    )
    return ApiResponse[SessionListResponse].success(payload)


# ──────────────── SES-03 详情 ────────────────


@router.get(
    "/{session_id}",
    response_model=ApiResponse[SessionDetail],
)
async def get_session_detail(
    session_id: uuid.UUID, db: DBSessionDep
) -> ApiResponse[SessionDetail]:
    """查询会话详情（V1.5 SES-03）。不存在返回 404 + code=40400。"""
    session = await session_service.get_session_or_raise(db, session_id)
    return ApiResponse[SessionDetail].success(
        SessionDetail.from_orm_session(session)
    )


# ──────────────── SES-04 改标题 ────────────────


@router.patch(
    "/{session_id}",
    response_model=ApiResponse[SessionDetail],
)
async def update_session(
    session_id: uuid.UUID,
    body: SessionUpdateRequest,
    db: DBSessionDep,
) -> ApiResponse[SessionDetail]:
    """更新会话标题（V1.5 SES-04）。仅 title 字段可改，传其他字段会被 Pydantic 拦截。"""
    session = await session_service.update_session_title(
        db, session_id, title=body.title
    )
    logger.info("会话标题已更新: id=%s title=%r", session.id, session.title)
    return ApiResponse[SessionDetail].success(
        SessionDetail.from_orm_session(session)
    )


# ──────────────── SES-05 删除 ────────────────


@router.delete(
    "/{session_id}",
    response_model=ApiResponse[None],
)
async def delete_session(
    session_id: uuid.UUID, db: DBSessionDep
) -> ApiResponse[None]:
    """物理删除会话及其全部消息（V1.5 SES-05）。关联 Milvus / Neo4j 数据不受影响。"""
    await session_service.delete_session(db, session_id)
    logger.info("会话已删除: id=%s", session_id)
    return ApiResponse[None].success(None, message="会话已删除")


# ──────────────── SES-06 历史消息（游标翻页） ────────────────


@router.get(
    "/{session_id}/messages",
    response_model=ApiResponse[MessageListResponse],
)
async def list_session_messages(
    session_id: uuid.UUID,
    db: DBSessionDep,
    limit: int = Query(20, ge=1, le=100, description="每页条数（1~100）"),
    before: uuid.UUID | None = Query(
        None, description="游标：返回该消息 ID 之前（更早）的消息"
    ),
) -> ApiResponse[MessageListResponse]:
    """分页返回历史消息（V1.5 SES-06），按 created_at 正序。"""
    items, has_more, next_before = await session_service.list_session_messages(
        db, session_id, limit=limit, before=before
    )
    payload = MessageListResponse(
        items=[MessageItem.model_validate(m) for m in items],
        has_more=has_more,
        next_before=next_before,
    )
    return ApiResponse[MessageListResponse].success(payload)
