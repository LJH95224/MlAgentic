"""Agent mock runner 的纯单元测试（不依赖 DB / 网络）。

3.3 阶段升级为 LangGraph 后，这些用例的契约（事件类型、出现顺序）
将作为回归基线被保留。
"""

import uuid

import pytest

from app.agent.runner import (
    AgentDone,
    AgentTextChunk,
    AgentToolEnd,
    AgentToolStart,
    run_stream,
)


@pytest.mark.asyncio
async def test_run_stream_emits_text_tool_and_done():
    """mock runner 应至少产出：文本块 → tool_start → tool_end → done。"""
    events = []
    async for ev in run_stream(uuid.uuid4(), "hello"):
        events.append(ev)

    kinds = [type(e) for e in events]

    # 必须包含全部四类
    assert AgentTextChunk in kinds
    assert AgentToolStart in kinds
    assert AgentToolEnd in kinds
    assert AgentDone in kinds

    # 顺序约束：tool_start 必须在 tool_end 之前；done 必须在最后
    assert kinds.index(AgentToolStart) < kinds.index(AgentToolEnd)
    assert kinds[-1] is AgentDone


@pytest.mark.asyncio
async def test_run_stream_user_input_appears_in_text():
    """mock runner 应在文本输出中回显用户输入（便于前端验证链路）。"""
    user_input = "测试雷达回波"
    parts = []
    async for ev in run_stream(uuid.uuid4(), user_input):
        if isinstance(ev, AgentTextChunk):
            parts.append(ev.content)

    joined = "".join(parts)
    assert user_input in joined