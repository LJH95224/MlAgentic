"""LangGraph 图节点实现（AGT-02 / AGT-03 / AGT-04）。

两个核心节点：
  - call_model_node: LLM 推理（含熔断检查）
  - tool_node:       工具执行（含错误反思）

一个路由函数：
  - should_continue: 根据最新消息决定下一步走向（tool_node 或 END）
"""

import json
import logging
import traceback
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END

from app.agent.state import AgentState
from app.tools import get_tool_map

logger = logging.getLogger(__name__)

# 熔断时返回给用户的兜底回复
_FALLBACK_REPLY = (
    "抱歉，我在尝试回答这个问题时陷入了过多的中间步骤，已强制终止。"
    "请尝试将问题拆分得更具体，或重新表述。"
)


def make_call_model_node(llm_with_tools):
    """工厂函数：闭包绑定 LLM 实例并返回 call_model 节点。

    封装为工厂主要是为了让测试方便替换 LLM。
    """

    async def call_model(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """LLM 推理节点。

        流程：
          1. 检查 remaining_iterations，若已耗尽则注入兜底回复并直接返回
             （AGT-03 死循环熔断）
          2. 调用 llm_with_tools.ainvoke(messages)，流式由 LangGraph
             stream_mode="messages" 自动捕获 token 级 chunk
          3. 把响应消息追加进 state，并将 remaining_iterations 减 1
        """
        remaining = state.get("remaining_iterations", 0)

        if remaining <= 0:
            logger.warning("ReAct 熔断触发：remaining_iterations 已耗尽")
            return {
                "messages": [AIMessage(content=_FALLBACK_REPLY)],
                "remaining_iterations": 0,
            }

        messages = state["messages"]
        response = await llm_with_tools.ainvoke(messages, config=config)

        logger.info(
            "call_model 完成：tool_calls=%s remaining=%d",
            len(getattr(response, "tool_calls", []) or []),
            remaining - 1,
        )
        return {
            "messages": [response],
            "remaining_iterations": remaining - 1,
        }

    return call_model


async def tool_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """工具执行节点（AGT-04 错误反思）。

    流程：
      1. 取最后一条 AIMessage 中的 tool_calls
      2. 对每个 tool_call 按名称查工具并执行
         - 成功：ToolMessage(content=result, status="success")
         - 失败：ToolMessage(content=traceback, status="error")
           —— 不吞异常，把堆栈喂给模型让它纠正后重试
      3. 通过 get_stream_writer() 发射控制流事件，供 runner 翻译为 SSE tool_end

    注意：工具调用的 tool_start 事件由 runner 在 stream_mode="messages"
    检测到 tool_call_chunks 时同步发出；这里只负责 tool_end。
    """
    last_msg: BaseMessage = state["messages"][-1]
    tool_calls = getattr(last_msg, "tool_calls", None) or []
    if not tool_calls:
        # 路由保证不会进到这里，留个兜底
        return {"messages": []}

    tool_map = get_tool_map()
    writer = get_stream_writer()
    new_messages: list[BaseMessage] = []

    for tc in tool_calls:
        # LangChain ToolCall 是 TypedDict: {name, args, id}
        name = tc["name"]
        args = tc.get("args", {})
        tc_id = tc["id"]

        tool = tool_map.get(name)
        if tool is None:
            # 模型调用了未注册的工具 —— 也作为错误反思回去
            err_content = f"工具 `{name}` 未注册。可用工具: {list(tool_map)}"
            logger.warning("未知工具调用: %s", name)
            new_messages.append(
                ToolMessage(content=err_content, tool_call_id=tc_id, name=name, status="error")
            )
            writer({"kind": "tool_end", "tool": name, "output": err_content, "error": True})
            continue

        try:
            # langchain 工具的标准异步入口
            result = await tool.ainvoke(args, config=config)
            # 工具返回值可能是 dict/list/str；统一序列化便于模型阅读
            content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            new_messages.append(
                ToolMessage(content=content, tool_call_id=tc_id, name=name)
            )
            logger.info("工具执行成功: %s output_len=%d", name, len(content))
            writer({"kind": "tool_end", "tool": name, "output": _truncate(content)})

        except Exception as e:
            # AGT-04: 不静默吞异常，把堆栈反馈给模型
            tb = traceback.format_exc()
            err_content = (
                f"工具 `{name}` 执行失败：{type(e).__name__}: {e}\n"
                f"调用参数：{json.dumps(args, ensure_ascii=False)}\n"
                f"堆栈：\n{tb}\n"
                f"请检查参数是否正确，必要时调整后重试。"
            )
            logger.error("工具执行异常: %s args=%s\n%s", name, args, tb)
            new_messages.append(
                ToolMessage(content=err_content, tool_call_id=tc_id, name=name, status="error")
            )
            writer({"kind": "tool_end", "tool": name, "output": str(e), "error": True})

    return {"messages": new_messages}


def should_continue(state: AgentState) -> str:
    """条件边路由：根据最新消息决定下一步。

    - 若 AIMessage 含 tool_calls → 进入 tool_node
    - 否则（纯文本最终回复或熔断兜底）→ END
    """
    last_msg = state["messages"][-1]
    tool_calls = getattr(last_msg, "tool_calls", None) or []
    if tool_calls:
        return "tools"
    return END


def _truncate(s: str, limit: int = 200) -> str:
    """截断长字符串用于日志/SSE 控制流摘要。"""
    return s if len(s) <= limit else s[:limit] + "..."
