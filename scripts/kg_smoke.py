"""KG 端到端联调脚本（手动运行，会烧 LLM token）。

用途：
  - KG-01：验证冷启动 + 约束就绪 + 健康检查
  - KG-03：直接调 query_knowledge_graph Tool
  - KG-04：Agent 端到端验证两步调用都有 tool_start
  - KG-05：依赖前置 rag_ingest.py 已写入 NER 实体

运行方式：
  conda activate geo_agent
  cd TyAgent
  # 第一次跑前先入库（会同时写 Milvus + Neo4j）：
  python scripts/rag_ingest.py
  # 然后跑联调：
  python scripts/kg_smoke.py

前置条件：
  - 本地 Neo4j 已启动并已通过 rag_ingest.py 写入实体
  - .env 中 NEO4J_* / LITELLM_* / EMBEDDING_* 均已正确配置
"""

import asyncio
import sys
import uuid
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app.agent.runner import (
    AgentDone,
    AgentTextChunk,
    AgentToolEnd,
    AgentToolStart,
    run_stream,
)
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.kg import close_neo4j, init_neo4j, query_knowledge_graph
from app.rag import close_milvus, init_milvus


# ──────────────────── 用例 1：直接调图谱查询 Tool ────────────────────


async def case_direct_query(entity_name: str = "台风"):
    """直接调 query_knowledge_graph，绕过 LLM 验证 Cypher 链路。"""
    print("\n" + "─" * 60)
    print(f"用例 1：直接调用 query_knowledge_graph(entity_name={entity_name!r})")
    print("─" * 60)

    result = await query_knowledge_graph.ainvoke({"entity_name": entity_name})
    print(result)


# ──────────────────── 用例 2：带过滤的图谱查询 ────────────────────


async def case_query_with_filters(entity_name: str = "台风"):
    """传入 entity_type + max_hops 验证过滤生效。"""
    print("\n" + "─" * 60)
    print(f"用例 2：带过滤 entity_name={entity_name!r} entity_type='LOCATION' max_hops=3")
    print("─" * 60)

    result = await query_knowledge_graph.ainvoke(
        {"entity_name": entity_name, "entity_type": "LOCATION", "max_hops": 3}
    )
    print(result)


# ──────────────────── 用例 3：Agent 端到端 Graph RAG ────────────────────


async def run_agent(prompt: str):
    """跑一次完整的 Agent 流式输出。

    同时记录 tool_start 与 tool_end 事件中的工具名 —— 因为 LangGraph
    在一次 call_model 出多个 tool_calls 时，tool_start 偶尔会合并/丢失
    （流式 chunk 切分时机问题），但 tool_end 是按实际执行结果发出的，
    更可靠。用集合 union 兜底，确保任一来源命中即算调用过。
    """
    print(f"\n>>> 用户: {prompt}")
    print("─" * 60)

    tools_started: list[str] = []   # 来自 tool_start 事件
    tools_ended: list[str] = []     # 来自 tool_end 事件（更可靠）

    async for event in run_stream(uuid.uuid4(), prompt):
        if isinstance(event, AgentTextChunk):
            print(event.content, end="", flush=True)
        elif isinstance(event, AgentToolStart):
            tools_started.append(event.tool)
            print(f"\n[🔧 工具调用开始] {event.tool}", flush=True)
        elif isinstance(event, AgentToolEnd):
            tools_ended.append(event.tool)
            preview = (event.output or "")[:120].replace("\n", " ")
            print(f"\n[✅ 工具调用结束] {event.tool} -> {preview}...", flush=True)
        elif isinstance(event, AgentDone):
            print(f"\n─ 完成（最终长度: {len(event.final_content)} 字符）─")

    return tools_started, tools_ended


async def case_agent_end_to_end():
    """用例 3：Agent 端到端 —— 期望模型分两步调用图谱 + 向量库。

    KG-04 验收点：两个工具（query_knowledge_graph 与 search_knowledge_base）
    都被实际调用。同时检测 tool_start 与 tool_end 两类事件，任一来源出现
    即视为该工具被使用过（流式 chunk 中 tool_start 偶尔会被合并）。
    """
    print("\n" + "═" * 60)
    print("用例 3：Agent 端到端（KG-04 联合查询验收）")
    print("═" * 60)

    started, ended = await run_agent(
        "查一下知识图谱里‘台风’这个实体，找到相关实体后再到知识库里检索对应原文。"
    )

    print("\n" + "─" * 60)
    print(f"tool_start 事件:  {started}")
    print(f"tool_end   事件:  {ended}")

    # 合并两类事件，做联合判定
    all_tools = set(started) | set(ended)
    kg_called = "query_knowledge_graph" in all_tools
    rag_called = "search_knowledge_base" in all_tools

    if kg_called and rag_called:
        print("✅ KG-04 验收通过：query_knowledge_graph 与 search_knowledge_base 都被实际调用")
    elif kg_called:
        print("⚠️  仅调用了 query_knowledge_graph，未调用 search_knowledge_base")
        print("   可能原因：图谱已能直接回答，或模型未理解 system prompt 中的联合查询提示")
    elif rag_called:
        print("⚠️  仅调用了 search_knowledge_base，未调用 query_knowledge_graph")
    else:
        print("❌ 未观测到任何工具调用 —— 检查 system prompt 与 LLM 模型能力")


# ──────────────────── 主流程 ────────────────────


async def main():
    setup_logging(debug=False)
    settings = get_settings()

    print("=" * 60)
    print("TyAgent 3.6 Neo4j 知识图谱联调脚本")
    print("=" * 60)
    print(f"Neo4j URI:         {settings.neo4j_uri}")
    print(f"Neo4j user:        {settings.neo4j_user}")
    print(f"Neo4j database:    {settings.neo4j_database}")
    print(f"Milvus Collection: {settings.milvus_collection}")
    print(f"NER model:         {settings.kg_ner_model or settings.litellm_model}")
    print(f"LLM model:         {settings.litellm_model}")

    # KG-01：初始化 —— 第二次启动应看到日志显示约束已存在跳过
    print("\n[KG-01] 初始化 Neo4j...")
    await init_neo4j()
    print("[KG-01] 初始化完成")

    # 用例 3 需要 Milvus 检索能力，也初始化一下
    init_milvus()

    try:
        await case_direct_query()
        await case_query_with_filters()
        await case_agent_end_to_end()
    finally:
        close_milvus()
        await close_neo4j()

    print("\n" + "=" * 60)
    print("联调结束")


if __name__ == "__main__":
    asyncio.run(main())
