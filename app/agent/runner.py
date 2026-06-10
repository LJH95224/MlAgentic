"""Agent 流式执行器（基于 LangGraph）。

V1.0（3.3 阶段）：在保持对外签名稳定的前提下，把 3.1 的 mock 实现替换为
真实的 LangGraph ReAct 引擎。

对外契约（与 mock 时期完全一致，service 层无感知）：
    run_stream(session_id, user_input, history=None) -> AsyncIterator[AgentEvent]

内部流程：
    1. 将 history（OpenAI dict 格式）转为 LangChain BaseMessage 列表
    2. 追加当前 user_input
    3. 调用 graph.astream(stream_mode=["messages", "custom"])
    4. 把 LangGraph 的流事件翻译为 AgentEvent 序列
"""

import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.agent.graph import get_compiled_graph
from app.core.config import get_settings

logger = logging.getLogger(__name__)


# ────────────── Agent 内部事件（与 3.1 mock 阶段保持一致） ──────────────


@dataclass
class AgentTextChunk:
    """文本块（一个或多个 token）。"""

    content: str = ""
    kind: Literal["text"] = "text"


@dataclass
class AgentToolStart:
    """工具开始执行。"""

    tool: str = ""
    args: dict | None = None
    kind: Literal["tool_start"] = "tool_start"


@dataclass
class AgentToolEnd:
    """工具执行结束。"""

    tool: str = ""
    output: str | None = None
    kind: Literal["tool_end"] = "tool_end"


@dataclass
class AgentDone:
    """运行完成。"""

    final_content: str = ""
    kind: Literal["done"] = "done"


AgentEvent = AgentTextChunk | AgentToolStart | AgentToolEnd | AgentDone


# ────────────── 历史消息格式转换 ──────────────


# Agent 系统提示词。每次会话开头注入，引导模型正确使用工具。
# 关键设计：
# - 工具用途与适用场景的明确分工说明
# - KG-04 联合查询的推荐模式（先 KG 锚定实体 → 再 RAG 精筛原文）
# - 防止模型陷入连续重试同一工具（触发熔断）
_SYSTEM_PROMPT = """你是一个具备主动检索能力的智能助手。你有以下工具可用：

1. **search_knowledge_base(query, top_k, doc_type?, document_id?, entity_tags?)**
   - 用途：在向量知识库中按语义检索文本片段
   - 适用：用户问的是事实/数据/原文，需要从知识库取证
   - 返回：编号片段列表，含 score 与来源 doc

2. **query_knowledge_graph(entity_name, entity_type?, relation_types?, max_hops?)**
   - 用途：在知识图谱中查询实体的关联路径
   - 适用：用户问"X 和 Y 之间的关系"或"X 涉及哪些相关实体"
   - 返回：路径列表，形如 "A → REL → B"

3. **mock_weather_parser(station_id, date)**：mock 气象站数据查询，仅用于测试

## 工具使用准则

**单工具场景**：
- 纯事实/语义查询 → 直接调 search_knowledge_base
- 纯关系/多跳推理 → 直接调 query_knowledge_graph

**Graph RAG 联合场景**（用户问题既涉及实体关系又需要原文支撑）：
1. 先调 query_knowledge_graph 拿到相关实体列表
2. 再调 search_knowledge_base，把上一步得到的实体名传入 entity_tags 精筛

**重要约束**：
- 同一工具最多重复调用 2 次。如果两次都没拿到满意结果，应该换一种工具或基于已有信息直接回答
- 不要无限重试同一查询，会触发熔断
- 拿到足够信息后，立即综合输出最终答案，不要继续调工具
"""


def _dict_to_message(d: dict) -> BaseMessage | None:
    """把 OpenAI 风格的 dict 消息转成 LangChain BaseMessage。

    跳过不识别的 role；assistant 含 tool_calls 时也保留（让模型理解历史的完整链路）。
    """
    role = d.get("role")
    content = d.get("content") or ""
    if role == "user":
        return HumanMessage(content=content)
    if role == "system":
        return SystemMessage(content=content)
    if role == "assistant":
        tool_calls = d.get("tool_calls") or []
        # LangChain 期望的 tool_calls 是 {name, args(dict), id} 格式
        normalized = []
        for tc in tool_calls:
            fn = tc.get("function", {})
            args = fn.get("arguments", "{}")
            if isinstance(args, str):
                import json as _json

                try:
                    args = _json.loads(args)
                except _json.JSONDecodeError:
                    args = {"_raw": args}
            normalized.append({"name": fn.get("name", ""), "args": args, "id": tc.get("id", "")})
        return AIMessage(content=content, tool_calls=normalized)
    if role == "tool":
        return ToolMessage(
            content=content,
            tool_call_id=d.get("tool_call_id", ""),
            name=d.get("name", ""),
        )
    return None


def _build_initial_messages(
    user_input: str,
    history: list[dict] | None,
) -> list[BaseMessage]:
    """构造首次进入图时的消息列表。

    首条强制为 SystemMessage（工具使用准则），避免模型陷入工具循环。
    若 history 已含 system 消息，则跳过默认的（用户/上游自定义优先）。
    """
    msgs: list[BaseMessage] = []

    # 检查 history 是否已含 system 消息
    history_has_system = any(
        (d.get("role") == "system") for d in (history or [])
    )
    if not history_has_system:
        msgs.append(SystemMessage(content=_SYSTEM_PROMPT))

    if history:
        for d in history:
            m = _dict_to_message(d)
            if m is not None:
                msgs.append(m)
    msgs.append(HumanMessage(content=user_input))
    return msgs


# ────────────── 主入口 ──────────────


async def run_stream(
    session_id: uuid.UUID,
    user_input: str,
    history: list[dict] | None = None,
) -> AsyncIterator[AgentEvent]:
    """执行一次 ReAct 推理，流式产出 AgentEvent。

    Args:
        session_id: 会话 ID（当前用于日志，未来可作为 LangGraph thread_id）
        user_input: 本轮用户输入
        history: 历史消息（OpenAI dict 格式），可为 None
    """
    settings = get_settings()
    graph = get_compiled_graph()

    initial_state = {
        "messages": _build_initial_messages(user_input, history),
        "remaining_iterations": settings.agent_max_iterations,
    }

    logger.info(
        "Agent 启动: session=%s history=%d max_iter=%d",
        session_id,
        len(history or []),
        settings.agent_max_iterations,
    )

    # 累积输出文本，作为最终回复落库
    final_text_parts: list[str] = []
    # 跟踪已发出 tool_start 的 tool_call index，避免增量 chunk 中重复发射。
    # 注意：以 index 为键，因为 id / name 可能分散在不同 chunk 中先后出现，
    # 而 LangChain 保证同一工具调用的 index 在所有 chunk 中一致。
    started_tool_indices: set[int] = set()
    # 缓存每个 index 对应的工具名，在 name 字段首次出现时即可发射 tool_start
    index_to_name: dict[int, str] = {}
    # 对于一次性返回的完整 AIMessage（无 tool_call_chunks），用 id 去重
    started_tool_call_ids: set[str] = set()

    async for mode, payload in graph.astream(
        initial_state,
        stream_mode=["messages", "custom"],
    ):
        if mode == "messages":
            # payload = (chunk: AIMessage | AIMessageChunk, metadata: dict)
            # 注意：AIMessageChunk 是 AIMessage 的子类。某些场景下 LangGraph
            # 可能吐出非 chunk 的完整 AIMessage（如某次调用未走流式），
            # 用 AIMessage 检查可同时兼容两种。
            chunk, _meta = payload
            if not isinstance(chunk, AIMessage):
                continue

            # 1) 文本（流式 chunk 和完整 AIMessage 都通过 content 暴露）
            if chunk.content:
                # content 偶尔会是 list（多 part 内容），统一为 str
                text = chunk.content if isinstance(chunk.content, str) else _flatten_content(chunk.content)
                if text:
                    final_text_parts.append(text)
                    yield AgentTextChunk(content=text)

            # 2) 工具调用 —— 兼容两条路径：
            #    a) AIMessageChunk: tool_call_chunks 增量，name 可能后到
            #    b) 完整 AIMessage:  tool_calls 一次性齐全
            tcc = getattr(chunk, "tool_call_chunks", None) or []
            if tcc:
                for tc_chunk in tcc:
                    idx = tc_chunk.get("index")
                    if idx is None:
                        continue
                    # 累积 name（同一 index 跨多 chunk 出现时，后续覆盖前面的空值）
                    name_piece = tc_chunk.get("name")
                    if name_piece:
                        index_to_name[idx] = name_piece
                    if idx in started_tool_indices:
                        continue
                    full_name = index_to_name.get(idx)
                    if not full_name:
                        continue
                    started_tool_indices.add(idx)
                    yield AgentToolStart(tool=full_name, args=None)
            else:
                # 路径 b：没有 tool_call_chunks，但完整 tool_calls 可能存在
                for tc in getattr(chunk, "tool_calls", None) or []:
                    tc_id = tc.get("id") or ""
                    if tc_id and tc_id in started_tool_call_ids:
                        continue
                    name = tc.get("name")
                    if not name:
                        continue
                    if tc_id:
                        started_tool_call_ids.add(tc_id)
                    yield AgentToolStart(tool=name, args=tc.get("args"))

        elif mode == "custom":
            # payload = tool_node 通过 get_stream_writer() 发出的 dict
            if isinstance(payload, dict) and payload.get("kind") == "tool_end":
                yield AgentToolEnd(
                    tool=payload.get("tool", ""),
                    output=payload.get("output"),
                )

    final_content = "".join(final_text_parts)
    logger.info("Agent 完成: session=%s output_len=%d", session_id, len(final_content))
    yield AgentDone(final_content=final_content)


def _flatten_content(content_parts) -> str:
    """把 LangChain 多 part 内容（list[dict]）扁平化为纯文本。"""
    out: list[str] = []
    for p in content_parts or []:
        if isinstance(p, str):
            out.append(p)
        elif isinstance(p, dict):
            t = p.get("text") or p.get("content")
            if isinstance(t, str):
                out.append(t)
    return "".join(out)
