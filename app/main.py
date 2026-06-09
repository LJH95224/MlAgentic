"""FastAPI 应用主入口。

启动方式：
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

设计说明：
- 使用 lifespan 替代已废弃的 on_event 钩子，集中管理资源生命周期。
- V1.0 阶段：启动时 create_all 建表（仅包含已显式导入的模型；
  knowledge_chunks 因依赖 pgvector，待 3.5 阶段切换为 alembic 后启用）。
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.session import engine
from app.models.base import Base

# 必须导入模型才能让 Base.metadata 感知到它们
from app.models import ChatMessage, ChatSession  # noqa: F401

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动建表，关闭释放连接。"""
    settings = get_settings()
    setup_logging(debug=settings.app_debug)

    logger.info("应用启动 env=%s debug=%s", settings.app_env, settings.app_debug)

    # 启动时建表（开发态，不替代 alembic）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库表初始化完成")

    yield

    # 关闭时释放连接池
    await engine.dispose()
    logger.info("应用已停止")


def create_app() -> FastAPI:
    """应用工厂。"""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description="GeoAgent V1.0 - 气象空间智能体基础后端引擎",
        version="0.1.0",
        lifespan=lifespan,
    )

    # 路由挂载
    app.include_router(v1_router)

    # 健康检查
    @app.get("/health", tags=["健康检查"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()