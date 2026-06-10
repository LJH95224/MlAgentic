"""LangGraph ReAct 图构建与 LLM 初始化。

图结构：
    START → call_model → should_continue
                            ├─ "tools" → tool_node → call_model（循环）
                            └─ END

LLM 接入：
  使用 langchain_openai.ChatOpenAI，配置 base_url 指向 DeepSeek（或其他
  OpenAI 兼容端点）。共用 3.2 阶段 .env 中的 LITELLM_MODEL / API_KEY / API_BASE。
"""

import logging
from functools import lru_cache

from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START

from app.agent.nodes import make_call_model_node, should_continue, tool_node
from app.agent.state import AgentState
from app.core.config import get_settings
from app.tools import get_tools

logger = logging.getLogger(__name__)


def _strip_model_prefix(model: str) -> str:
    """ChatOpenAI 不需要 LiteLLM 风格的厂商前缀，剥除 `deepseek/` 这类前缀。"""
    if "/" in model:
        _, _, name = model.partition("/")
        return name
    return model


def _build_llm():
    """根据 Settings 构造 ChatOpenAI 实例。

    注意：LangChain 的 ChatOpenAI 接受 OpenAI 协议的 base_url；
    与 LiteLLM 不同，模型名不需要 `deepseek/` 前缀。
    """
    settings = get_settings()
    if not settings.litellm_model:
        raise ValueError("LITELLM_MODEL 未配置")

    model_name = _strip_model_prefix(settings.litellm_model)
    logger.info(
        "初始化 ChatOpenAI: model=%s api_base=%s timeout=%.1fs",
        model_name,
        settings.litellm_api_base,
        settings.litellm_timeout,
    )
    return ChatOpenAI(
        model=model_name,
        api_key=settings.litellm_api_key,
        base_url=settings.litellm_api_base,
        timeout=settings.litellm_timeout,
        max_retries=settings.litellm_num_retries,
        # streaming=True 让 .ainvoke 在 stream_mode="messages" 下也能产出 chunk
        streaming=True,
    )


@lru_cache(maxsize=1)
def get_compiled_graph():
    """获取编译后的 LangGraph 图（进程内单例）。

    使用 lru_cache 避免每次请求都重建图与 LLM 实例（建模 client 有开销）。
    """
    llm = _build_llm()
    tools = get_tools()
    llm_with_tools = llm.bind_tools(tools)

    workflow = StateGraph(AgentState)
    workflow.add_node("call_model", make_call_model_node(llm_with_tools))
    workflow.add_node("tools", tool_node)

    workflow.add_edge(START, "call_model")
    workflow.add_conditional_edges("call_model", should_continue, {"tools": "tools", "__end__": "__end__"})
    workflow.add_edge("tools", "call_model")

    graph = workflow.compile()
    logger.info("LangGraph 图编译完成，已注册工具: %s", [t.name for t in tools])
    return graph


def reset_graph_cache() -> None:
    """清空已缓存的图实例（测试 / 配置变更场景使用）。"""
    get_compiled_graph.cache_clear()
