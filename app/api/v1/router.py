"""V1 API 路由聚合。

所有 /api/v1/ 下的端点集中注册于此。
"""

from fastapi import APIRouter

from app.api.v1.endpoints import chat, sessions

router = APIRouter(prefix="/api/v1")

router.include_router(sessions.router)
router.include_router(chat.router)