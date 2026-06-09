"""LLM 网关模块（3.2 阶段：LiteLLM 统一接入层）。

对外核心接口：
  - acompletion(messages, tools?) -> dict        一次性返回
  - astream(messages, tools?) -> AsyncIterator    流式返回

消息与工具构造辅助：
  - messages.user / system / assistant / tool_result
  - messages.define_tool
"""

from app.llm.client import acompletion, astream
from app.llm.messages import (
    Message,
    ToolDefinition,
    define_tool,
    system,
    user,
    assistant,
    tool_result,
)

__all__ = [
    "acompletion",
    "astream",
    "Message",
    "ToolDefinition",
    "define_tool",
    "user",
    "system",
    "assistant",
    "tool_result",
]
