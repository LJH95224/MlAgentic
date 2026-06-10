"""Agent runner 的纯单元测试（不依赖 DB / 网络 / 真 LLM）。

通过 mock 掉 graph.astream，验证 runner 把 LangGraph 流事件正确翻译为 AgentEvent。
"""

import uuid
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from app.agent.runner import (
    AgentDone,
    AgentTextChunk,
    AgentToolEnd,
    AgentToolStart,
    run_stream,
)


def _make_text_chunk(text: str) -> AIMessageChunk:
    """构造一个仅含文本的 AIMessageChunk。"""
    return AIMessageChunk(content=text)


def _make_tool_call_chunk(tc_id: str, name: str) -> AIMessageChunk:
    """构造一个含 tool_call_chunks 的 AIMessageChunk。"""
    return AIMessageChunk(
        content="",
        tool_call_chunks=[
            {"name": name, "args": "{}", "id": tc_id, "index": 0}
        ],
    )


def _make_split_tool_chunks(name: str) -> list[AIMessageChunk]:
    """模拟某些 LLM 把 tool_call 拆成两个 chunk 的情况：
    第一个 chunk 只带 index，第二个 chunk 才带 name。
    """
    return [
        AIMessageChunk(
            content="",
            tool_call_chunks=[{"name": None, "args": "", "id": None, "index": 0}],
        ),
        AIMessageChunk(
            content="",
            tool_call_chunks=[{"name": name, "args": '{"x":1}', "id": "call_late", "index": 0}],
        ),
    ]


class _FakeGraph:
    """假图：按构造时给定的事件序列产出。"""

    def __init__(self, events):
        self._events = events

    def astream(self, *_args, **_kwargs):
        events = self._events

        async def gen():
            for e in events:
                yield e

        return gen()


@pytest.mark.asyncio
async def test_runner_translates_text_chunks():
    """文本 chunk 应被翻译为 AgentTextChunk，且最后 yield AgentDone。"""
    events = [
        ("messages", (_make_text_chunk("你好"), {})),
        ("messages", (_make_text_chunk("，世界"), {})),
    ]
    fake = _FakeGraph(events)

    with patch("app.agent.runner.get_compiled_graph", return_value=fake):
        out = [e async for e in run_stream(uuid.uuid4(), "hi")]

    text_events = [e for e in out if isinstance(e, AgentTextChunk)]
    assert [e.content for e in text_events] == ["你好", "，世界"]

    assert isinstance(out[-1], AgentDone)
    assert out[-1].final_content == "你好，世界"


@pytest.mark.asyncio
async def test_runner_emits_tool_start_only_once_per_id():
    """同一 tool_call_id 出现多次增量时，AgentToolStart 只发一次。"""
    events = [
        ("messages", (_make_tool_call_chunk("call_1", "mock_weather_parser"), {})),
        # 第二个 chunk 重复同一 id，runner 应忽略
        ("messages", (_make_tool_call_chunk("call_1", "mock_weather_parser"), {})),
    ]
    fake = _FakeGraph(events)

    with patch("app.agent.runner.get_compiled_graph", return_value=fake):
        out = [e async for e in run_stream(uuid.uuid4(), "hi")]

    starts = [e for e in out if isinstance(e, AgentToolStart)]
    assert len(starts) == 1
    assert starts[0].tool == "mock_weather_parser"


@pytest.mark.asyncio
async def test_runner_emits_tool_start_when_name_arrives_in_later_chunk():
    """边缘 case：tool_call 拆成多个 chunk，name 在第二个 chunk 才出现。

    runner 应基于 index 追踪，name 一到就发 AgentToolStart（仅一次）。
    """
    chunks = _make_split_tool_chunks("mock_weather_parser")
    events = [("messages", (c, {})) for c in chunks]
    fake = _FakeGraph(events)

    with patch("app.agent.runner.get_compiled_graph", return_value=fake):
        out = [e async for e in run_stream(uuid.uuid4(), "hi")]

    starts = [e for e in out if isinstance(e, AgentToolStart)]
    assert len(starts) == 1
    assert starts[0].tool == "mock_weather_parser"



    """custom mode 中的 tool_end 应翻译为 AgentToolEnd。"""
    events = [
        ("custom", {"kind": "tool_end", "tool": "mock_weather_parser", "output": "{...}"}),
    ]
    fake = _FakeGraph(events)

    with patch("app.agent.runner.get_compiled_graph", return_value=fake):
        out = [e async for e in run_stream(uuid.uuid4(), "hi")]

    ends = [e for e in out if isinstance(e, AgentToolEnd)]
    assert len(ends) == 1
    assert ends[0].tool == "mock_weather_parser"
    assert ends[0].output == "{...}"


@pytest.mark.asyncio
async def test_runner_full_react_cycle():
    """完整 ReAct 链路：text → tool_start → tool_end → text → done。"""
    events = [
        ("messages", (_make_text_chunk("让我查一下..."), {})),
        ("messages", (_make_tool_call_chunk("call_1", "mock_weather_parser"), {})),
        ("custom", {"kind": "tool_end", "tool": "mock_weather_parser", "output": "ok"}),
        ("messages", (_make_text_chunk("北京"), {})),
        ("messages", (_make_text_chunk("25 度"), {})),
    ]
    fake = _FakeGraph(events)

    with patch("app.agent.runner.get_compiled_graph", return_value=fake):
        out = [e async for e in run_stream(uuid.uuid4(), "天气如何？")]

    kinds = [type(e) for e in out]
    # 顺序：text, tool_start, tool_end, text, text, done
    assert AgentToolStart in kinds
    assert AgentToolEnd in kinds
    # tool_start 必须在 tool_end 之前
    assert kinds.index(AgentToolStart) < kinds.index(AgentToolEnd)
    assert kinds[-1] is AgentDone
    # 累积文本
    final = out[-1].final_content
    assert "让我查一下..." in final
    assert "北京" in final
    assert "25 度" in final


def test_history_dict_to_message_conversion():
    """历史消息（dict 格式）应正确转换为 BaseMessage。"""
    from app.agent.runner import _dict_to_message

    msg = _dict_to_message({"role": "user", "content": "你好"})
    assert msg.content == "你好"

    msg = _dict_to_message({"role": "assistant", "content": "你好！"})
    assert msg.content == "你好！"

    msg = _dict_to_message({"role": "system", "content": "you are an agent"})
    assert msg.content == "you are an agent"

    # 不识别的 role 返回 None
    assert _dict_to_message({"role": "unknown", "content": "x"}) is None


def test_history_assistant_with_tool_calls():
    """历史里的 assistant + tool_calls 应正确转换。"""
    from app.agent.runner import _dict_to_message

    raw = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_x",
                "type": "function",
                "function": {"name": "mock_weather_parser", "arguments": '{"station_id": "54511", "date": "2026-06-09"}'},
            }
        ],
    }
    msg = _dict_to_message(raw)
    assert msg.tool_calls
    assert msg.tool_calls[0]["name"] == "mock_weather_parser"
    assert msg.tool_calls[0]["args"] == {"station_id": "54511", "date": "2026-06-09"}


@pytest.mark.asyncio
async def test_runner_emits_tool_start_for_complete_ai_message():
    """边缘 case：LangGraph 吐出完整 AIMessage（非 chunk）时，
    应通过 tool_calls 路径正常发射 AgentToolStart（多轮对话场景常见）。
    """
    complete = AIMessage(
        content="",
        tool_calls=[{"name": "mock_weather_parser", "args": {"x": 1}, "id": "call_full"}],
    )
    events = [("messages", (complete, {}))]
    fake = _FakeGraph(events)

    with patch("app.agent.runner.get_compiled_graph", return_value=fake):
        out = [e async for e in run_stream(uuid.uuid4(), "hi")]

    starts = [e for e in out if isinstance(e, AgentToolStart)]
    assert len(starts) == 1
    assert starts[0].tool == "mock_weather_parser"
    assert starts[0].args == {"x": 1}

