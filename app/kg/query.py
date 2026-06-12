"""Neo4j 多跳查询实现（KG-03 内部）。

不直接对接 LLM，仅提供纯 async 函数；@tool 包装放在 app/kg/tool.py。

Cypher 设计要点：
- 变长路径 [r*1..N] 中 N 不能参数化，需要 f-string 拼接，因此 max_hops
  必须先夹值到 [1, 5] 防注入与防爆炸
- 双向遍历（不指定方向），覆盖 (Entity)-[MENTIONED_IN]->(Document)
  和 (Entity)-[RELATED_TO]-(Entity) 两类关系
- LIMIT 20 防止图谱爆炸
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.core.config import get_settings

if TYPE_CHECKING:
    from neo4j import AsyncDriver, AsyncManagedTransaction

logger = logging.getLogger(__name__)


_MAX_HOPS_CAP = 5
_RESULT_LIMIT = 20


def _clamp_hops(max_hops: int) -> int:
    """夹值 max_hops 到 [1, 5]。"""
    if max_hops < 1:
        return 1
    if max_hops > _MAX_HOPS_CAP:
        return _MAX_HOPS_CAP
    return max_hops


def build_cypher(
    entity_type: str | None,
    relation_types: list[str] | None,
    max_hops: int,
    kb_ids: list[str] | None = None,
) -> str:
    """根据可选过滤构造 Cypher 文本（max_hops 必须已 clamp）。

    单独成函数，让单测能直接断言 Cypher 文本而不必跑真 Neo4j。

    V1.5 KB-06：传入 kb_ids 时追加 `start.kb_id IN $kb_ids` 过滤——只查这些
    KB 的子图，不污染其它 KB 的实体。kb_ids=None 时不过滤（V1.0 行为）。
    """
    where_clauses: list[str] = []
    if entity_type:
        where_clauses.append("start.type = $entity_type")
    if relation_types:
        where_clauses.append("ALL(rel IN r WHERE type(rel) IN $rel_types)")
    if kb_ids:
        # V1.5 KB-06：起点实体必须属于指定的 KB
        where_clauses.append("start.kb_id IN $kb_ids")

    where_block = ""
    if where_clauses:
        where_block = "WHERE " + " AND ".join(where_clauses) + "\n"

    # 变长路径 [r*1..N]：N 不能参数化，但已 clamp 到 [1,5] 安全
    return (
        f"MATCH path = (start:Entity {{name: $name}})-[r*1..{max_hops}]-(neighbor)\n"
        f"{where_block}"
        f"RETURN start.name AS start, start.type AS start_type,\n"
        f"       [n IN nodes(path) | "
        f"{{name: coalesce(n.name, n.document_id), "
        f"type: coalesce(n.type, labels(n)[0])}}] AS nodes_in_path,\n"
        f"       [rel IN relationships(path) | type(rel)] AS rels_in_path,\n"
        f"       length(path) AS hops\n"
        f"LIMIT {_RESULT_LIMIT}"
    )


async def _query_tx(
    tx: "AsyncManagedTransaction",
    cypher: str,
    name: str,
    entity_type: str | None,
    rel_types: list[str] | None,
    kb_ids: list[str] | None = None,
) -> list[dict]:
    """事务函数：执行查询并把结果完全消费成 list[dict]。"""
    params: dict = {
        "name": name,
        "entity_type": entity_type,
        "rel_types": rel_types,
    }
    if kb_ids is not None:
        params["kb_ids"] = kb_ids
    result = await tx.run(cypher, **params)
    records = []
    async for record in result:
        records.append(dict(record))
    return records


async def execute_graph_query(
    driver: "AsyncDriver",
    entity_name: str,
    entity_type: str | None,
    relation_types: list[str] | None,
    max_hops: int,
    kb_ids: list[str] | None = None,
) -> list[dict]:
    """执行多跳图谱查询，返回原始 records。

    Args:
        driver: 已初始化的 AsyncDriver
        entity_name: 查询起点实体名
        entity_type: 可选，限定起点实体类型
        relation_types: 可选，限定路径上的关系类型列表
        max_hops: 路径最大跳数（自动 clamp 到 1-5）
        kb_ids: V1.5 KB-06，可选 list[str(UUID)]，限定起点实体属于这些 KB

    Returns:
        每条形如 {"start":..., "start_type":..., "nodes_in_path":[...],
                 "rels_in_path":[...], "hops": int}
    """
    settings = get_settings()
    hops = _clamp_hops(max_hops)
    cypher = build_cypher(entity_type, relation_types, hops, kb_ids=kb_ids)

    logger.info(
        "graph_query: entity=%r type=%s rels=%s hops=%d kb_ids=%s",
        entity_name,
        entity_type,
        relation_types,
        hops,
        kb_ids,
    )

    async with driver.session(database=settings.neo4j_database) as sess:
        return await sess.execute_read(
            _query_tx, cypher, entity_name, entity_type, relation_types, kb_ids
        )


# ──────────────────── 结果格式化 ────────────────────


def format_paths(
    entity_name: str,
    entity_type: str | None,
    records: list[dict],
) -> str:
    """把 records 格式化为 LLM 友好的字符串。

    形如：
        查询: "台风" (LOCATION)
        相关路径（共 3 条）:
        [1] 台风 → MENTIONED_IN → typhoon_paths
        [2] 台风 → RELATED_TO → 副热带高压 → MENTIONED_IN → typhoon_paths
    """
    header_type = f" ({entity_type})" if entity_type else ""
    if not records:
        return (
            f"查询: {entity_name!r}{header_type}\n"
            f"（图谱中未找到该实体的关联路径。可能原因：实体名拼写不一致、"
            f"或该实体尚未被入库流程抽取。建议换关键词或先确认 Neo4j 中是否存在该实体。）"
        )

    lines = [
        f"查询: {entity_name!r}{header_type}",
        f"相关路径（共 {len(records)} 条）:",
    ]
    for i, rec in enumerate(records, start=1):
        nodes = rec.get("nodes_in_path") or []
        rels = rec.get("rels_in_path") or []
        # 按 [node, rel, node, rel, node] 形式交错拼接
        parts: list[str] = []
        for j, node in enumerate(nodes):
            node_name = node.get("name", "?") if isinstance(node, dict) else str(node)
            parts.append(node_name)
            if j < len(rels):
                parts.append(f"→ {rels[j]} →")
        lines.append(f"[{i}] " + " ".join(parts))

    return "\n".join(lines)


__all__ = [
    "execute_graph_query",
    "build_cypher",
    "format_paths",
    "_clamp_hops",  # 暴露给单测
]
