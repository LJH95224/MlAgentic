"""V2 API 路由聚合。

所有 /api/v2/ 下的端点集中注册于此。
V1.5 的 /api/v1/ 完全不动，V2 在独立前缀下并存。
"""

from fastapi import APIRouter

from app.api.v2.endpoints import query, traces

router = APIRouter(prefix="/api/v2")

router.include_router(traces.router)
router.include_router(query.router)
