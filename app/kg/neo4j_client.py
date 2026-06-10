"""Neo4j 客户端单例 + 启动期初始化（KG-01）。

设计要点：
- 单例 AsyncDriver，全进程共享（Neo4j 官方文档要求每个应用只持有一个 driver 实例）。
- 启动时调用 init_neo4j()：连接 + verify_connectivity + 幂等创建唯一性约束。
- 关闭时调用 close_neo4j()：释放连接池。
- 不在模块顶层 import neo4j，便于无 neo4j 包环境下的单测/类型检查只跑纯逻辑模块。

并发说明：
- AsyncDriver 对协程并发安全（官方文档保证），但**不**线程安全。
  FastAPI 单进程 asyncio 模型下完全够用。
- 所有查询都走 async with driver.session(...) + execute_read/execute_write，
  事务函数会被自动重试，无需应用层兜底。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.core.config import get_settings

if TYPE_CHECKING:
    from neo4j import AsyncDriver, AsyncManagedTransaction

logger = logging.getLogger(__name__)


# 进程级单例。get_neo4j_driver() 首次调用前必须先调 init_neo4j()。
_driver: "AsyncDriver | None" = None


# ──────────────────── 约束定义（KG-01 必创建） ────────────────────

# 复合唯一键 (name, type)：同名实体可能不同语义（"苹果"既可能是 ORG 也可能是 OTHER），
# 单按 name 唯一会丢失语义；联合唯一既保持 MERGE 幂等，又允许多义词共存。
_CONSTRAINTS = [
    (
        "doc_id_unique",
        "CREATE CONSTRAINT doc_id_unique IF NOT EXISTS "
        "FOR (d:Document) REQUIRE d.document_id IS UNIQUE",
    ),
    (
        "entity_name_type_unique",
        "CREATE CONSTRAINT entity_name_type_unique IF NOT EXISTS "
        "FOR (e:Entity) REQUIRE (e.name, e.type) IS UNIQUE",
    ),
]


async def _create_constraints(tx: "AsyncManagedTransaction") -> None:
    """事务函数：批量创建约束（IF NOT EXISTS 保证幂等）。

    单独成函数是为了走 execute_write 的自动重试。
    """
    for name, cypher in _CONSTRAINTS:
        await tx.run(cypher)
        logger.debug("约束已就绪: %s", name)


# ──────────────────── 单例管理 ────────────────────


async def init_neo4j() -> "AsyncDriver":
    """连接 Neo4j，验证连通性，幂等创建唯一性约束。

    重复调用安全：已建连时直接返回单例。

    Raises:
        RuntimeError: 连接失败时透传底层异常并附环境信息
    """
    global _driver

    if _driver is not None:
        return _driver

    # 局部导入，避免无 neo4j 包环境时模块加载失败
    from neo4j import AsyncGraphDatabase

    settings = get_settings()

    logger.info(
        "Neo4j 连接初始化: uri=%s user=%s database=%s",
        settings.neo4j_uri,
        settings.neo4j_user,
        settings.neo4j_database,
    )

    _driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )

    try:
        await _driver.verify_connectivity()
    except Exception as e:
        # 不吞异常 —— 启动期连不上 Neo4j 应该让应用直接挂掉
        await _driver.close()
        _driver = None
        raise RuntimeError(
            f"Neo4j 连接失败 uri={settings.neo4j_uri}：{e}"
        ) from e

    # 幂等建约束（KG-01 验收点）
    async with _driver.session(database=settings.neo4j_database) as sess:
        await sess.execute_write(_create_constraints)
    logger.info("Neo4j 唯一性约束已就绪（Document.document_id / Entity(name,type)）")

    return _driver


def get_neo4j_driver() -> "AsyncDriver":
    """获取已初始化的 AsyncDriver 单例。

    Raises:
        RuntimeError: 调用前未执行 init_neo4j()
    """
    if _driver is None:
        raise RuntimeError(
            "Neo4j 驱动尚未初始化。请先在应用启动时调用 await init_neo4j()。"
        )
    return _driver


async def close_neo4j() -> None:
    """释放 Neo4j 连接池。应用关闭时调用，幂等。"""
    global _driver
    if _driver is None:
        return

    try:
        await _driver.close()
    except Exception as e:  # noqa: BLE001
        # 关闭阶段静默记录，不阻断 lifespan
        logger.warning("Neo4j 关闭异常（已忽略）：%s", e)
    finally:
        _driver = None
        logger.info("Neo4j 连接已释放")


__all__ = ["init_neo4j", "get_neo4j_driver", "close_neo4j"]
