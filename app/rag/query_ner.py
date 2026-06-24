"""Query 侧 NER + 图谱锚定（HRE-02）。

PRD §HRE-02 描述：检索开始前从 Query 抽实体 → Neo4j 查邻接实体 → 注入 Milvus
``entity_tags`` 标量过滤，是 Graph RAG 的核心链路。

设计要点：
1. **薄封装** [app/kg/ner.py](../kg/ner.py) ``run_ner``：prompt 不动；后续如需切换更轻量的
   Query 侧 NER 模型，只需在本模块切换实现，调用方零感知。
2. **硬超时**：每个 LLM / Neo4j 调用都包 ``asyncio.wait_for``，避免单点慢调用拖死整批。
3. **软失败**：异常/超时一律返 [] / []，不阻断主链路；记 warning 写日志。
4. **并发限流**：多实体查 Neo4j 时用 ``Semaphore(5)``，防压垮 Neo4j。
5. **entity_tags 长度安全**：Milvus VARCHAR ``max_length`` 是 UTF-8 字节数（不是字符数），
   按字节安全截断到 64 字节，最终标签上限 50 个（与 Schema cap 对齐）。

调用方：[app/api/v2/endpoints/query.py](../api/v2/endpoints/query.py) 的 v2_query。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.core.async_utils import gather_with_timeout
from app.core.config import get_settings
from app.kg.ner import run_ner
from app.kg.neo4j_client import get_neo4j_driver
from app.kg.query import execute_graph_query

if TYPE_CHECKING:
    from neo4j import AsyncDriver

logger = logging.getLogger(__name__)


# ──────────────────── 常量 ────────────────────

# Milvus entity_tags 字段：ARRAY<VARCHAR(64)>, capacity=50（schema.py 规范）
_MAX_TAG_BYTES = 64
_MAX_TAGS = 50

# Neo4j 并发限流：单次 Query 触发的多实体并发查询不应压垮 Neo4j
_NEO4J_CONCURRENCY = 5

# 图谱锚定固定单跳；多跳容易爆炸，且 PRD §HRE-02 示例就是单跳邻居
_ANCHOR_HOPS = 1


# ──────────────────── 工具函数 ────────────────────


def _truncate_utf8(s: str, max_bytes: int) -> str:
    """按 UTF-8 字节安全截断（中文 3 字节/字）。

    参考项目记忆 [[milvus-varchar-max-length-is-bytes]]：Milvus VARCHAR max_length
    是字节数，中文 22 字 = 66 字节超 entity_tags(max_length=64)，必须按字节截断。
    """
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    # 反复试切到首个合法 UTF-8 边界
    truncated = encoded[:max_bytes]
    while truncated:
        try:
            return truncated.decode("utf-8")
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return ""


# ──────────────────── 主入口 ────────────────────


async def extract_query_entities(query: str) -> list[dict]:
    """从 Query 抽取实体。薄封装 [app/kg/ner.py](../kg/ner.py) ``run_ner``，加硬超时。

    Args:
        query: 用户原始查询文本

    Returns:
        实体列表 ``[{"name":..., "type":...}]``，已去重；
        异常/超时/空 query 一律返 ``[]``（软失败）。
    """
    if not query or not query.strip():
        return []

    settings = get_settings()
    timeout = settings.query_ner_timeout_s

    try:
        return await asyncio.wait_for(run_ner(query), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("Query NER 超时（%.1fs），软失败返空", timeout)
        return []
    except Exception as e:  # noqa: BLE001
        # run_ner 本身已经是软失败（内部吞异常返 []），这里再加一层兜底防意外
        logger.warning("Query NER 调用失败（已忽略）：%s: %s", type(e).__name__, e)
        return []


async def _anchor_one_entity(
    driver: "AsyncDriver",
    entity_name: str,
    kb_ids: list[str] | None,
    timeout: float,
    sem: asyncio.Semaphore,
) -> list[str]:
    """对单个实体查 Neo4j 邻接，返回邻居实体名（含起点自身）；失败返 []。"""
    async with sem:
        try:
            records = await asyncio.wait_for(
                execute_graph_query(
                    driver=driver,
                    entity_name=entity_name,
                    entity_type=None,        # Query 侧不限实体类型
                    relation_types=None,
                    max_hops=_ANCHOR_HOPS,
                    kb_ids=kb_ids,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("图谱锚定单实体超时 entity=%r timeout=%.1fs",
                           entity_name, timeout)
            return []
        except Exception as e:  # noqa: BLE001
            logger.warning("图谱锚定单实体失败 entity=%r err=%s: %s",
                           entity_name, type(e).__name__, e)
            return []

    # 收集路径上所有节点的 name（含 Document 节点的 document_id 不要）
    names: list[str] = []
    for rec in records:
        for node in rec.get("nodes_in_path", []) or []:
            ntype = node.get("type")
            name = node.get("name")
            # 仅保留 Entity 节点；过滤 Document 节点（label 而非实体类型）
            if not name or ntype == "Document":
                continue
            names.append(name)
    return names


async def anchor_to_graph(
    entities: list[dict],
    kb_ids: list[str] | None,
) -> list[str]:
    """对 Query NER 抽出的实体并发查 Neo4j 邻接，返回 entity_tags 列表。

    流程：
    1. 对每个实体起 ``execute_graph_query(max_hops=1, kb_ids=kb_ids)``，并发 Semaphore=5
    2. 路径上的所有 Entity 节点 name 收集 → 按 UTF-8 字节安全截断到 64 → 去重 → 上限 50

    Args:
        entities: NER 输出的实体列表 ``[{"name":..., "type":...}]``；空时直接返 []
        kb_ids: KB 隔离过滤列表（KB-06 已支持）；None 表示不过滤

    Returns:
        ``entity_tags`` 列表，可直接传给 ``hybrid_search(entity_tags=...)``；
        空列表表示「未命中图谱 / 锚定全部失败」，调用方应短路不传 entity_tags 参数。
    """
    if not entities:
        return []

    settings = get_settings()
    timeout = settings.graph_anchor_timeout_s

    try:
        driver = get_neo4j_driver()
    except Exception as e:  # noqa: BLE001
        # Neo4j 未启动 / driver 未初始化 → 软失败
        logger.warning("图谱锚定无法获取 Neo4j driver（已忽略）：%s: %s",
                       type(e).__name__, e)
        return []

    sem = asyncio.Semaphore(_NEO4J_CONCURRENCY)
    coros = [
        _anchor_one_entity(
            driver=driver,
            entity_name=ent.get("name", ""),
            kb_ids=kb_ids,
            timeout=timeout,
            sem=sem,
        )
        for ent in entities
        if ent.get("name")
    ]

    if not coros:
        return []

    # gather 所有实体的邻接结果；单实体已自捕获，外层再加整组硬超时兜底
    try:
        results = await gather_with_timeout(
            coros,
            timeout_s=max(timeout + 5, len(coros) * timeout / _NEO4J_CONCURRENCY + 5),
            label="query_graph_anchor",
            return_exceptions=True,
        )
    except asyncio.TimeoutError:
        logger.warning("图谱锚定整组超时（已忽略） entity_count=%d", len(coros))
        return []

    # 仅收集 Neo4j 真正命中的标签（_anchor_one_entity 内部已含起点实体 name）。
    # 不再强行把"NER 抽出但图谱里不存在"的实体加入 tags ——否则会导致
    # `ARRAY_CONTAINS_ANY` 硬过滤把所有 chunk 都过滤掉（chunks 上的 entity_tags
    # 是入库时 NER 抽的，与 Query 侧 NER 词面常常对不上）。
    tags: list[str] = []
    for r in results:
        if isinstance(r, Exception):
            continue
        tags.extend(r)

    # 去重 + 字节截断 + 上限
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw_name in tags:
        if not raw_name:
            continue
        truncated = _truncate_utf8(str(raw_name), _MAX_TAG_BYTES)
        if not truncated or truncated in seen:
            continue
        seen.add(truncated)
        cleaned.append(truncated)
        if len(cleaned) >= _MAX_TAGS:
            break

    return cleaned


__all__ = [
    "extract_query_entities",
    "anchor_to_graph",
]
