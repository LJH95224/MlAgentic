"""Celery 入库任务的资源工厂（V1.5 S3.2 决策：每任务新建 + 断）。

为什么不复用 app.db.session / app.rag.milvus_client / app.kg.neo4j_client 的全局单例？
- 全局单例是为 FastAPI 进程设计：进程内长生命周期、随 lifespan 一次性 init/close
- Celery worker 是另一种生命周期：
  - prefork 模式下父进程 fork 出子进程，父进程的 socket / 连接池 fork 给子进程后副本无效，
    子进程第一次用就崩
  - solo 模式下虽然没 fork，但任务异常导致 event loop 异常后旧连接同样会卡死
- 最稳的解决方案就是"每个任务的 asyncio.run 入口里现建 + 退出前 dispose/close"，多 ~200ms
  握手但完全规避 fork 副作用 + event loop 跨任务复用问题

本模块提供唯一入口：

    async with task_resources() as resources:
        async with resources.db() as session:
            ...
        resources.milvus.search(...)
        async with resources.neo4j.session(...) as sess:
            ...
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

if TYPE_CHECKING:
    from neo4j import AsyncDriver
    from pymilvus import MilvusClient

logger = logging.getLogger(__name__)


class TaskResources:
    """单次入库任务持有的所有外部资源。

    用 dataclass 而不是 NamedTuple 是为了延迟初始化 + db() 工厂方法。
    """

    def __init__(
        self,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession],
        milvus: "MilvusClient",
        neo4j: "AsyncDriver",
    ) -> None:
        self._engine = engine
        self._session_factory = session_factory
        self.milvus = milvus
        self.neo4j = neo4j

    @asynccontextmanager
    async def db(self) -> AsyncIterator[AsyncSession]:
        """开一个干净的 PG session；用 async with 自动 close。

        每个步骤一个独立 session，避免持有过久导致事务冲突。
        """
        async with self._session_factory() as session:
            yield session


@asynccontextmanager
async def task_resources() -> AsyncIterator[TaskResources]:
    """单次任务的资源生命周期管理：进入时全部建好，退出时全部释放。

    Yields:
        TaskResources：含 db 工厂、milvus client、neo4j driver

    Raises:
        ImportError: 缺 pymilvus / neo4j（生产环境必装；测试可 mock task_resources）
        Exception: 任一外部服务连不上时透传，让上层任务 status=failed
    """
    settings = get_settings()

    # ── 1) PG engine（NullPool，每次 connect/disconnect）─────────────────
    connect_args = (
        {"ssl": False} if "+asyncpg" in settings.database_url else {}
    )
    engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
        connect_args=connect_args,
        future=True,
    )
    session_factory = async_sessionmaker(
        bind=engine, expire_on_commit=False, class_=AsyncSession
    )

    # ── 2) Milvus client（局部导入避免无 pymilvus 环境 import 错误）──────
    from pymilvus import MilvusClient

    milvus = MilvusClient(
        uri=settings.milvus_uri,
        token=settings.milvus_token or "",
    )

    # ── 3) Neo4j async driver ───────────────────────────────────────────
    from neo4j import AsyncGraphDatabase

    neo4j_driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )

    logger.info(
        "TaskResources 已就绪 pg=%s milvus=%s neo4j=%s",
        settings.database_url.split("@")[-1],
        settings.milvus_uri,
        settings.neo4j_uri,
    )

    try:
        yield TaskResources(engine, session_factory, milvus, neo4j_driver)
    finally:
        # ── 反向释放：neo4j → milvus → pg ─────────────────────────────
        try:
            await neo4j_driver.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("TaskResources Neo4j close 失败: %s", e)
        try:
            milvus.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("TaskResources Milvus close 失败: %s", e)
        try:
            await engine.dispose()
        except Exception as e:  # noqa: BLE001
            logger.warning("TaskResources PG dispose 失败: %s", e)
        logger.info("TaskResources 已释放")


__all__ = ["TaskResources", "task_resources"]
