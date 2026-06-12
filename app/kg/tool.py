"""query_knowledge_graph 工具（KG-03 + V1.5 KB-06）。

Agent 在 ReAct 循环中主动调用：
- 当问题涉及实体之间的关系、间接关联、多跳推理时使用
- 与 search_knowledge_base 互补：图谱锚定 + 向量精筛 = Graph RAG（KG-04）

V1.5 KB-06：从 contextvar 读 current_kb_ids 决定查哪些 KB 的子图，
LLM 不可见 kb_ids 参数（由 endpoint 层从用户请求注入）。

异常透传：底层错误抛回 LangGraph tool_node，由它转为 ToolMessage(status="error")
触发 AGT-04 错误反思链路 —— 本层不要吞异常。
"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from app.agent.context import get_current_kb_ids
from app.kg.neo4j_client import get_neo4j_driver
from app.kg.query import execute_graph_query, format_paths

logger = logging.getLogger(__name__)


@tool
async def query_knowledge_graph(
    entity_name: str,
    entity_type: str | None = None,
    relation_types: list[str] | None = None,
    max_hops: int = 2,
) -> str:
    """查询知识图谱中与给定实体相关的实体关系网络。

    适用场景：
    - 问题涉及"实体之间的关系"或"间接关联"（如"台风和数值预报模式有什么关系？"）
    - 需要追溯实体出现的源文档（→ 再用 search_knowledge_base 拿原文）
    - 多跳推理（如"通过哪些中间实体可以从 A 到 B"）

    与 search_knowledge_base 的差异：
    - 本工具返回结构化的实体路径（图谱关系）
    - search_knowledge_base 返回原始文本片段（语义相似）
    - 推荐组合：先用本工具找到相关实体名 → 再用 search_knowledge_base(entity_tags=[...]) 精筛

    Args:
        entity_name: 起点实体名（必须精确匹配 Neo4j 中的 Entity.name）
        entity_type: 可选，限定起点实体类型，取值 PERSON / LOCATION / ORG / TIME / OTHER
        relation_types: 可选，限定路径上的关系类型，如 ["MENTIONED_IN"] 或 ["RELATED_TO"]
        max_hops: 多跳层数，默认 2，可调范围 1~5（超过会被夹到 5）

    Returns:
        格式化的路径字符串。每条形如 "[N] start → REL → mid → REL → end"。
        无命中时返回提示文本。
    """
    # V1.5 KB-06：从 contextvar 读 kb_ids 范围（LLM 不可见）
    # 提前判 kb_ids=[] 早返 —— 不必拿 driver、不查 Neo4j
    # None / [] / [...] 三种语义与 retriever 对齐：
    #   None  → 不过滤（V1.0 行为）
    #   []    → 显式不查（直接返无结果）
    #   [...] → 限定到指定 KB
    current_kb_ids = get_current_kb_ids()
    if current_kb_ids is not None and len(current_kb_ids) == 0:
        logger.info(
            "query_knowledge_graph: kb_ids=[] 显式空，跳过图谱查询 entity=%r",
            entity_name,
        )
        return (
            f"查询: {entity_name!r}\n"
            f"（本轮对话未指定知识库，无可查询的图谱。）"
        )

    driver = get_neo4j_driver()

    kb_ids_str: list[str] | None = (
        [str(k) for k in current_kb_ids] if current_kb_ids else None
    )

    records = await execute_graph_query(
        driver=driver,
        entity_name=entity_name,
        entity_type=entity_type,
        relation_types=relation_types,
        max_hops=max_hops,
        kb_ids=kb_ids_str,
    )
    return format_paths(entity_name, entity_type, records)


__all__ = ["query_knowledge_graph"]
