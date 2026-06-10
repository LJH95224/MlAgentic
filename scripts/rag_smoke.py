"""RAG 端到端联调脚本（手动运行，会烧 Embedding token 和 LLM token）。

用途：
  - RAG-01：验证冷启动连接 + Collection 自动建/复用 + load
  - RAG-02 / 03 / 04 / 05：手动调 search_knowledge_base 验证检索链路
  - RAG-02 + ReAct：让 Agent 自主决定调用工具，端到端验证

运行方式：
  conda activate geo_agent
  cd TyAgent
  # 第一次跑前先入库：
  python scripts/rag_ingest.py
  # 然后跑联调：
  python scripts/rag_smoke.py

前置条件：
  - 本地 Milvus 已启动，data/seed 已通过 rag_ingest.py 入库
  - .env 中 LITELLM_* 与 EMBEDDING_* 均已正确配置
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
from app.rag import close_milvus, init_milvus, search_knowledge_base


# ──────────────────── 用例 1：直接调 Tool，验证检索 ────────────────────


async def case_direct_search():
    """用例 1：直接调 search_knowledge_base 工具（绕过 LLM）。

    验收点：
      - 命中结果含 document_id（RAG-05）
      - 含权限过滤（RAG-04）但无显式参数 —— 走内部 ALL 注入
    """
    print("\n" + "─" * 60)
    print("用例 1：直接调用 Tool（无过滤）")
    print("─" * 60)

    result = await search_knowledge_base.ainvoke(
        {"query": "台风路径如何预报", "top_k": 3}
    )
    print(result)


# ──────────────────── 用例 2：带 doc_type 过滤 ────────────────────


async def case_filter_by_doc_type():
    """用例 2：传入 doc_type='report' 验证标量过滤生效（RAG-03）。"""
    print("\n" + "─" * 60)
    print("用例 2：带 doc_type='report' 过滤")
    print("─" * 60)

    result = await search_knowledge_base.ainvoke(
        {"query": "雷达 短时强降水", "top_k": 3, "doc_type": "report"}
    )
    print(result)


# ──────────────────── 用例 3：Agent 端到端 ────────────────────


async def run_agent(prompt: str):
    """跑一次完整的 Agent 流式输出。"""
    print(f"\n>>> 用户: {prompt}")
    print("─" * 60)

    async for event in run_stream(uuid.uuid4(), prompt):
        if isinstance(event, AgentTextChunk):
            print(event.content, end="", flush=True)
        elif isinstance(event, AgentToolStart):
            print(f"\n[🔧 工具调用开始] {event.tool}", flush=True)
        elif isinstance(event, AgentToolEnd):
            preview = (event.output or "")[:120].replace("\n", " ")
            print(f"\n[✅ 工具调用结束] {event.tool} -> {preview}...", flush=True)
        elif isinstance(event, AgentDone):
            print(f"\n─ 完成（最终长度: {len(event.final_content)} 字符）─")


async def case_agent_end_to_end():
    """用例 3：Agent 端到端 —— 让 LLM 自主决定是否调用 search_knowledge_base。

    验收点（RAG-02）：模型应当主动输出符合参数规范的 tool_call。
    """
    print("\n" + "═" * 60)
    print("用例 3：Agent 端到端（LLM 自主调 search_knowledge_base）")
    print("═" * 60)

    await run_agent(
        "我想了解一下西北太平洋台风的常见路径有哪些，请基于知识库回答。"
    )


# ──────────────────── 主流程 ────────────────────


async def main():
    setup_logging(debug=False)  # smoke 时关掉 debug 日志，输出更清爽
    settings = get_settings()

    print("=" * 60)
    print("TyAgent 3.5 Milvus RAG 联调脚本")
    print("=" * 60)
    print(f"Milvus URI:        {settings.milvus_uri}")
    print(f"Collection:        {settings.milvus_collection}")
    print(f"Embedding model:   {settings.embedding_model}")
    print(f"Embedding dim:     {settings.embedding_dimension}")
    print(f"LLM model:         {settings.litellm_model}")
    print(f"Default role:      {settings.rag_default_role}")

    # RAG-01：初始化 Milvus —— 首次运行会建库，二次运行会跳过
    print("\n[RAG-01] 初始化 Milvus...")
    init_milvus()
    print("[RAG-01] 初始化完成（首次创建 / 已存在复用 见日志）")

    try:
        await case_direct_search()
        await case_filter_by_doc_type()
        await case_agent_end_to_end()
    finally:
        close_milvus()

    print("\n" + "=" * 60)
    print("联调结束")


if __name__ == "__main__":
    asyncio.run(main())
