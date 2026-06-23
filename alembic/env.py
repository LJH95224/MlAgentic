"""Alembic 迁移环境（async 版本）。

为什么是 async：项目 SQLAlchemy 引擎走 `postgresql+asyncpg`（详见 [app/db/session.py]），
直接用 alembic 默认的 sync env.py 会因 asyncpg 不实现 DBAPI 而炸。
官方推荐范式：`async_engine_from_config` + `connection.run_sync(do_run_migrations)`。

URL 注入策略：alembic.ini 的 `sqlalchemy.url` 故意留空，这里从
`app.core.config.get_settings().database_url` 拿 —— 单一来源（与 .env 同源），
不存在 alembic.ini 跟 .env 漂移的可能。
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ⚠️ 顺序很关键：必须先 import 所有模型，触发它们注册到 Base.metadata，
#    `target_metadata = Base.metadata` 才能完整反映 schema。漏一个 import
#    autogenerate 就会少一张表 / 少一个外键。
from app.core.config import get_settings
from app.models import (  # noqa: F401
    AgentTrace,
    ChatMessage,
    ChatSession,
    EvalTask,
    KbFile,
    KnowledgeBase,
    QueryAnalytics,
)
from app.models.base import Base

# alembic Config 对象（从 alembic.ini 解析）
config = context.config

# 配置日志（沿用 alembic.ini 中的 [loggers]/[handlers]/[formatters]）
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 注入实际 DB URL（覆盖 alembic.ini 中故意留空的 sqlalchemy.url）
config.set_main_option("sqlalchemy.url", get_settings().database_url)

# autogenerate 的对照基准
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """offline 模式：不连库，只把 SQL 打到 stdout（CI 审查 / 生产 DBA 审计用）。

    生成方式：`alembic upgrade head --sql > migration.sql`
    """
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # 让 autogen 对类型 / server_default 变化都敏感（默认值过于宽松）
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """实际跑迁移的同步函数（在 async 连接的 run_sync 里被调）。"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """online 模式：连真实库执行迁移（开发 / 部署常用路径）。

    走 NullPool —— alembic 跑完即关，不留任何长连接，避免与 uvicorn 主进程
    的连接池抢配额；也规避 Windows asyncpg 偶发的 [WinError 121] 问题。
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # asyncpg 在 Windows 上 SSL prefer 偶发握手超时（详见 app/db/session.py 注释），
        # 这里同样关掉 SSL 探测
        connect_args={"ssl": False}
        if "+asyncpg" in get_settings().database_url
        else {},
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
