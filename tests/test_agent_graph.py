"""Agent 图节点单元测试（AGT-01 / AGT-03 / AGT-04 验收）。

验证：
- AgentState 与图编译 OK
- call_model 在 remaining_iterations 耗尽时返回兜底回复（AGT-03）
- tool_node 捕获工具异常并以 ToolMessage 形式回传（AGT-04）
- should_continue 路由正确
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END

from app.agent.nodes import make_call_model_node, should_continue, tool_node
from app.agent.state import AgentState


# ────────────── AGT-01：状态机定义 ──────────────


def test_agent_state_typeddict():
    """AgentState 应为 TypedDict，且必填字段齐全。"""
    s: AgentState = {
        "messages": [HumanMessage(content="hi")],
        "remaining_iterations": 5,
    }
    assert s["remaining_iterations"] == 5
    assert len(s["messages"]) == 1


# ────────────── AGT-03：熔断 ──────────────


@pytest.mark.asyncio
async def test_call_model_fuse_blocks_when_iterations_exhausted():
    """remaining_iterations <= 0 时应不调 LLM，直接返回兜底 AIMessage。"""
    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(side_effect=AssertionError("不应该被调用"))

    node = make_call_model_node(mock_llm)
    result = await node(
        {"messages": [HumanMessage(content="x")], "remaining_iterations": 0},
        config={},
    )

    mock_llm.ainvoke.assert_not_called()
    msgs = result["messages"]
    assert len(msgs) == 1
    assert isinstance(msgs[0], AIMessage)
    assert "终止" in msgs[0].content or "陷入" in msgs[0].content


@pytest.mark.asyncio
async def test_call_model_decrements_iterations():
    """每次 call_model 正常返回时 remaining_iterations 减 1。"""
    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content="ok"))

    node = make_call_model_node(mock_llm)
    result = await node(
        {"messages": [HumanMessage(content="x")], "remaining_iterations": 3},
        config={},
    )

    assert result["remaining_iterations"] == 2
    assert result["messages"][0].content == "ok"


# ────────────── AGT-04：错误反思 ──────────────


@tool
def _failing_tool(x: int) -> int:
    """一定会抛错的工具，用于测试错误反思。"""
    raise ValueError("故意失败")


@tool
def _ok_tool(value: str) -> str:
    """正常返回的工具。"""
    return f"echo:{value}"


@pytest.mark.asyncio
async def test_tool_node_catches_exception_into_toolmessage(monkeypatch):
    """工具抛异常时，tool_node 应返回包含堆栈的 ToolMessage 而非崩溃。"""
    # patch 工具注册中心
    monkeypatch.setattr(
        "app.agent.nodes.get_tool_map",
        lambda: {"_failing_tool": _failing_tool},
    )

    ai_msg = AIMessage(
        content="",
        tool_calls=[{"name": "_failing_tool", "args": {"x": 1}, "id": "call_a"}],
    )

    with patch("app.agent.nodes.get_stream_writer", return_value=lambda _x: None):
        result = await tool_node({"messages": [ai_msg]}, config={})

    msgs = result["messages"]
    assert len(msgs) == 1
    tm = msgs[0]
    assert isinstance(tm, ToolMessage)
    assert tm.status == "error"
    assert "_failing_tool" in tm.content
    assert "ValueError" in tm.content
    assert "故意失败" in tm.content


@pytest.mark.asyncio
async def test_tool_node_executes_success_path(monkeypatch):
    """工具正常返回时，tool_node 应返回 success 状态的 ToolMessage。"""
    monkeypatch.setattr(
        "app.agent.nodes.get_tool_map",
        lambda: {"_ok_tool": _ok_tool},
    )

    ai_msg = AIMessage(
        content="",
        tool_calls=[{"name": "_ok_tool", "args": {"value": "hi"}, "id": "call_b"}],
    )

    with patch("app.agent.nodes.get_stream_writer", return_value=lambda _x: None):
        result = await tool_node({"messages": [ai_msg]}, config={})

    tm = result["messages"][0]
    assert isinstance(tm, ToolMessage)
    # 成功路径不设 status="error"
    assert tm.status != "error"
    assert tm.content == "echo:hi"


@pytest.mark.asyncio
async def test_tool_node_unknown_tool(monkeypatch):
    """模型调用了未注册的工具，应返回错误反思而非崩溃。"""
    monkeypatch.setattr("app.agent.nodes.get_tool_map", lambda: {})

    ai_msg = AIMessage(
        content="",
        tool_calls=[{"name": "no_such_tool", "args": {}, "id": "call_c"}],
    )

    with patch("app.agent.nodes.get_stream_writer", return_value=lambda _x: None):
        result = await tool_node({"messages": [ai_msg]}, config={})

    tm = result["messages"][0]
    assert tm.status == "error"
    assert "未注册" in tm.content


# ────────────── 路由 ──────────────


def test_should_continue_routes_to_tools_when_tool_calls_present():
    last = AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": "1"}])
    assert should_continue({"messages": [last]}) == "tools"


def test_should_continue_routes_to_end_when_pure_text():
    last = AIMessage(content="最终回答")
    assert should_continue({"messages": [last]}) == END


# ────────────── 图编译 ──────────────


def test_graph_compiles_without_error(monkeypatch):
    """图应能成功编译（验证 AGT-01：LangGraph 图编译无报错）。"""
    # 避免真的创建 ChatOpenAI 实例（需要 API key 校验）
    fake_llm = AsyncMock()
    fake_llm.bind_tools = lambda _tools: fake_llm

    monkeypatch.setattr("app.agent.graph._build_llm", lambda: fake_llm)

    from app.agent.graph import get_compiled_graph, reset_graph_cache

    reset_graph_cache()
    try:
        graph = get_compiled_graph()
        assert graph is not None
        # 图应该包含我们注册的节点
        nodes = set(graph.get_graph().nodes.keys())
        assert "call_model" in nodes
        assert "tools" in nodes
    finally:
        reset_graph_cache()
