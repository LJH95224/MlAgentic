"""FastAPI 应用主入口。

启动方式：
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

设计说明：
- 使用 lifespan 替代已废弃的 on_event 钩子，集中管理资源生命周期。
- V1.0 阶段：启动时 create_all 建表（仅包含已显式导入的模型）。
  按新版 PRD，knowledge_chunks 由 Milvus 管理（详见 §3.5），不在 PostgreSQL 建表。
- 3.5 阶段：lifespan 启动时初始化 Milvus（连接 + 幂等建库 + load），关闭时释放。
- 3.6 阶段：lifespan 启动时初始化 Neo4j（连接 + 验证 + 幂等建约束），关闭时释放。
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.session import engine
from app.kg import close_neo4j, init_neo4j
from app.models.base import Base
from app.rag import close_milvus, init_milvus

# 必须导入模型才能让 Base.metadata 感知到它们
# V1.5：新增 KnowledgeBase / KbFile 两张表（PRD §5.2、§5.3）
from app.models import ChatMessage, ChatSession, KbFile, KnowledgeBase  # noqa: F401

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动建表 + 初始化 Milvus + Neo4j；关闭时反向释放。"""
    settings = get_settings()
    setup_logging(debug=settings.app_debug)

    logger.info("应用启动 env=%s debug=%s", settings.app_env, settings.app_debug)

    # 启动时建表（开发态，不替代 alembic）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库表初始化完成")

    # Milvus 初始化（同步调用，启动期 < 1s 可接受）
    # 失败会抛 RuntimeError，应用直接挂掉 —— 这是符合 fail-fast 原则的
    init_milvus()

    # Neo4j 初始化（异步，含 verify_connectivity + 幂等建约束）
    await init_neo4j()

    yield

    # 关闭顺序：反向释放 —— Neo4j → Milvus → PG
    await close_neo4j()
    close_milvus()
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