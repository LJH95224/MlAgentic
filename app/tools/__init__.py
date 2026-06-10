"""本地工具注册中心。

设计原则：
- 工具以 langchain_core.tools.BaseTool 形式注册（@tool 装饰器产出的对象）。
- get_tools() 返回所有已注册工具，供 LangGraph 在编译图时绑定到 LLM。
- get_tool_map() 返回 name → BaseTool 字典，供 tool_node 按名称查找并执行。
- 3.4 阶段：增加 subprocess 脚本调度工具。
- 3.5 阶段：增加 search_knowledge_base 工具。
- 3.6 阶段：增加 query_knowledge_graph 工具。
"""

from langchain_core.tools import BaseTool

from app.kg import query_knowledge_graph
from app.rag import search_knowledge_base
from app.tools.weather_parser import mock_weather_parser

# 已注册工具清单。
# 新增工具时：
#   1. 在 app/tools/ 下新建模块，用 @tool 装饰器定义
#   2. 在这里 import 并追加到列表
_REGISTERED_TOOLS: list[BaseTool] = [
    mock_weather_parser,
    search_knowledge_base,
    query_knowledge_graph,
]


def get_tools() -> list[BaseTool]:
    """获取所有已注册工具（用于绑定到 LLM）。"""
    return list(_REGISTERED_TOOLS)


def get_tool_map() -> dict[str, BaseTool]:
    """获取 name → BaseTool 的查找表（用于 tool_node 按名称执行）。"""
    return {t.name: t for t in _REGISTERED_TOOLS}


__all__ = ["get_tools", "get_tool_map"]
