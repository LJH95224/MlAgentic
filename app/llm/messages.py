"""LLM 消息与工具定义的辅助类型与构造器。

设计原则：
- 不重新发明消息结构 —— 直接复用 OpenAI / LiteLLM 的 dict 规范。
  这样可以让 LangGraph、LiteLLM、各家厂商 SDK 之间零适配。
- 提供 TypedDict + 构造函数，仅做"类型提示 + 减少手写错误"。
"""

from typing import Any, Literal, NotRequired, TypedDict

# ──────────────── 消息 ────────────────


class ToolCall(TypedDict):
    """模型在 assistant 消息里发出的工具调用。"""

    id: str
    type: Literal["function"]
    function: dict[str, Any]  # {"name": str, "arguments": str(JSON)}


class Message(TypedDict, total=False):
    """OpenAI Chat Completion 兼容消息。

    LiteLLM 透传到所有支持厂商，因此这就是项目的标准消息载体。
    各 role 必填字段：
      - system / user:  content
      - assistant:      content 与 tool_calls 至少一个非空
      - tool:           content 与 tool_call_id
    """

    role: Literal["system", "user", "assistant", "tool"]
    content: str | None
    tool_calls: NotRequired[list[ToolCall]]
    tool_call_id: NotRequired[str]
    name: NotRequired[str]


def user(content: str) -> Message:
    """构造用户消息。"""
    return {"role": "user", "content": content}


def system(content: str) -> Message:
    """构造系统消息。"""
    return {"role": "system", "content": content}


def assistant(content: str | None = None, tool_calls: list[ToolCall] | None = None) -> Message:
    """构造助手消息。content 与 tool_calls 至少一个非空。"""
    msg: Message = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def tool_result(tool_call_id: str, content: str, name: str | None = None) -> Message:
    """构造工具执行结果消息。

    Args:
        tool_call_id: 对应 assistant 消息中 tool_calls[i].id
        content: 工具输出（建议 JSON 字符串，便于模型解析）
        name: 工具名（可选，部分厂商要求）
    """
    msg: Message = {
        "role": "tool",
        "content": content,
        "tool_call_id": tool_call_id,
    }
    if name:
        msg["name"] = name
    return msg


# ──────────────── 工具定义 ────────────────


class ToolFunction(TypedDict):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


class ToolDefinition(TypedDict):
    """供 LLM 注册的工具描述。"""

    type: Literal["function"]
    function: ToolFunction


def define_tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
) -> ToolDefinition:
    """便捷构造一个 function 类型的工具定义。

    Args:
        name: 工具名（snake_case，模型会按此名称回调）
        description: 工具用途描述（喂给模型决定是否调用）
        parameters: 入参 JSON Schema，例如
            {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}
    """
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }
