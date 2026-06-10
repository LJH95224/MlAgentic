"""Neo4j 写入接口（KG-02）。

三个核心 Upsert：
- upsert_document：MERGE Document 节点（按 document_id 幂等）
- upsert_entity：MERGE Entity 节点（按 name + type 复合键幂等）
- link_entity_to_chunk：建 (Entity)-[:MENTIONED_IN {chunk_id:...}]->(Document) 关系

两个批量版本（rag_ingest.py 用）：
- bulk_upsert_entities：一次性 UNWIND 写入一批实体
- bulk_link_entities_to_chunk：一次性 UNWIND 建立一批关系

设计要点：
- 所有写入走 session.execute_write(tx_fn) 模式，享受自动重试
- Cypher 全部参数化，杜绝注入
- 单测用 mock driver 验证 Cypher 文本与参数，不依赖真 Neo4j
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.core.config import get_settings

if TYPE_CHECKING:
    from neo4j import AsyncDriver, AsyncManagedTransaction

logger = logging.getLogger(__name__)


# ──────────────────── Cypher 模板（导出供单测断言） ────────────────────

UPSERT_DOCUMENT_CYPHER = """
MERGE (d:Document {document_id: $document_id})
SET d.title = $title,
    d.created_at = coalesce(d.created_at, $created_at)
RETURN d.document_id AS document_id
""".strip()

UPSERT_ENTITY_CYPHER = """
MERGE (e:Entity {name: $name, type: $type})
ON CREATE SET e.document_ids = [$document_id]
ON MATCH SET e.document_ids =
    CASE
        WHEN $document_id IN coalesce(e.document_ids, [])
        THEN e.document_ids
        ELSE coalesce(e.document_ids, []) + $document_id
    END
RETURN e.name AS name, e.type AS type
""".strip()

LINK_ENTITY_TO_CHUNK_CYPHER = """
MATCH (e:Entity {name: $name, type: $type})
MATCH (d:Document {document_id: $document_id})
MERGE (e)-[r:MENTIONED_IN {chunk_id: $chunk_id}]->(d)
RETURN e.name AS name, d.document_id AS document_id, r.chunk_id AS chunk_id
""".strip()

# 批量版本：UNWIND $rows 把一批参数一次性下发
BULK_UPSERT_ENTITIES_CYPHER = """
UNWIND $rows AS row
MERGE (e:Entity {name: row.name, type: row.type})
ON CREATE SET e.document_ids = [row.document_id]
ON MATCH SET e.document_ids =
    CASE
        WHEN row.document_id IN coalesce(e.document_ids, [])
        THEN e.document_ids
        ELSE coalesce(e.document_ids, []) + row.document_id
    END
RETURN count(e) AS n
""".strip()

BULK_LINK_CYPHER = """
UNWIND $rows AS row
MATCH (e:Entity {name: row.name, type: row.type})
MATCH (d:Document {document_id: row.document_id})
MERGE (e)-[r:MENTIONED_IN {chunk_id: row.chunk_id}]->(d)
RETURN count(r) AS n
""".strip()


# ──────────────────── 单条 Upsert ────────────────────


async def _upsert_doc_tx(
    tx: "AsyncManagedTransaction",
    document_id: str,
    title: str,
    created_at: str | None,
) -> str:
    result = await tx.run(
        UPSERT_DOCUMENT_CYPHER,
        document_id=document_id,
        title=title,
        created_at=created_at,
    )
    record = await result.single()
    return record["document_id"]


async def upsert_document(
    driver: "AsyncDriver",
    document_id: str,
    title: str,
    created_at: str | None = None,
) -> str:
    """MERGE Document 节点。

    Args:
        driver: 已初始化的 AsyncDriver
        document_id: 文档唯一标识（与 Milvus chunks.document_id 对齐）
        title: 文档标题
        created_at: 创建时间（ISO 字符串），首次写入有效，后续 MERGE 不覆盖

    Returns:
        实际写入的 document_id
    """
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as sess:
        return await sess.execute_write(
            _upsert_doc_tx, document_id, title, created_at
        )


async def _upsert_entity_tx(
    tx: "AsyncManagedTransaction",
    name: str,
    entity_type: str,
    document_id: str,
) -> tuple[str, str]:
    result = await tx.run(
        UPSERT_ENTITY_CYPHER,
        name=name,
        type=entity_type,
        document_id=document_id,
    )
    record = await result.single()
    return record["name"], record["type"]


async def upsert_entity(
    driver: "AsyncDriver",
    name: str,
    entity_type: str,
    document_id: str,
) -> tuple[str, str]:
    """MERGE Entity 节点（按 name + type 复合键幂等）。

    同 document_id 多次出现同实体不会建多个节点；不同 document_id 出现同实体
    会扩充 e.document_ids 数组（去重）。
    """
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as sess:
        return await sess.execute_write(
            _upsert_entity_tx, name, entity_type, document_id
        )


async def _link_tx(
    tx: "AsyncManagedTransaction",
    name: str,
    entity_type: str,
    document_id: str,
    chunk_id: int,
) -> None:
    await tx.run(
        LINK_ENTITY_TO_CHUNK_CYPHER,
        name=name,
        type=entity_type,
        document_id=document_id,
        chunk_id=chunk_id,
    )


async def link_entity_to_chunk(
    driver: "AsyncDriver",
    entity_name: str,
    entity_type: str,
    document_id: str,
    chunk_id: int,
) -> None:
    """建立 (Entity)-[:MENTIONED_IN {chunk_id:...}]->(Document) 关系。

    chunk_id 作为关系属性，便于追溯出处切片（PRD §4.4）。
    MERGE 按 chunk_id 去重，重复调用幂等。
    """
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as sess:
        await sess.execute_write(
            _link_tx, entity_name, entity_type, document_id, chunk_id
        )


# ──────────────────── 批量版本（rag_ingest 用） ────────────────────


async def _bulk_upsert_entities_tx(
    tx: "AsyncManagedTransaction", rows: list[dict]
) -> int:
    result = await tx.run(BULK_UPSERT_ENTITIES_CYPHER, rows=rows)
    record = await result.single()
    return int(record["n"]) if record else 0


async def bulk_upsert_entities(
    driver: "AsyncDriver", rows: list[dict]
) -> int:
    """一次性写入一批实体。

    Args:
        rows: 每条形如 {"name": "...", "type": "...", "document_id": "..."}

    Returns:
        实际处理的实体数量
    """
    if not rows:
        return 0
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as sess:
        return await sess.execute_write(_bulk_upsert_entities_tx, rows)


async def _bulk_link_tx(
    tx: "AsyncManagedTransaction", rows: list[dict]
) -> int:
    result = await tx.run(BULK_LINK_CYPHER, rows=rows)
    record = await result.single()
    return int(record["n"]) if record else 0


async def bulk_link_entities_to_chunk(
    driver: "AsyncDriver", rows: list[dict]
) -> int:
    """一次性建立一批 MENTIONED_IN 关系。

    Args:
        rows: 每条形如 {"name":..., "type":..., "document_id":..., "chunk_id": int}
    """
    if not rows:
        return 0
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as sess:
        return await sess.execute_write(_bulk_link_tx, rows)


__all__ = [
    "upsert_document",
    "upsert_entity",
    "link_entity_to_chunk",
    "bulk_upsert_entities",
    "bulk_link_entities_to_chunk",
    # Cypher 模板导出供测试断言
    "UPSERT_DOCUMENT_CYPHER",
    "UPSERT_ENTITY_CYPHER",
    "LINK_ENTITY_TO_CHUNK_CYPHER",
    "BULK_UPSERT_ENTITIES_CYPHER",
    "BULK_LINK_CYPHER",
]
