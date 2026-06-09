"""会话端点（API-01）。

POST /api/v1/sessions  — 创建新会话
"""

import logging
from http import HTTPStatus

from fastapi import APIRouter

from app.api.deps import DBSessionDep
from app.schemas.session import CreateSessionResponse
from app.services import session_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["会话管理"])


@router.post("", response_model=CreateSessionResponse, status_code=HTTPStatus.CREATED)
async def create_session(db: DBSessionDep) -> CreateSessionResponse:
    """创建一个新的对话会话。

    返回：
        - 201: 创建成功，返回 session_id 与时间戳
    """
    session = await session_service.create_session(db)
    logger.info("会话已创建: %s", session.id)
    return CreateSessionResponse(id=session.id, created_at=session.created_at)