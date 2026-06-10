"""LangGraph 状态机定义（AGT-01）。

AgentState 是图运行期间在节点间传递的共享状态。
本项目的 ReAct 循环只需要两个字段：
  - messages: LangChain BaseMessage 列表，使用 add_messages 自动追加
  - remaining_iterations: 熔断倒计数（AGT-03），由 call_model_node 每次进入时递减
"""

from typing import Annotated, Sequence, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """ReAct Agent 的运行时状态。"""

    # 消息历史。add_messages 注解使 LangGraph 自动将新消息追加到列表，
    # 而不是覆盖。每个节点 return {"messages": [new_msg]} 即可。
    messages: Annotated[Sequence[BaseMessage], add_messages]

    # 剩余可用的 ReAct 循环次数。
    # 初始值 = settings.agent_max_iterations（默认 5）。
    # call_model_node 每次进入时：
    #   - 若 <= 0：注入兜底 AIMessage 并返回（熔断），不再调 LLM
    #   - 否则递减并继续
    remaining_iterations: int
