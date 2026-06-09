"""Agent 流式执行器。

V1.0 阶段（3.1）：此处提供一个 mock 实现，用于打通 SSE 链路并验证 API-03
（控制流与文本流的混合下发）。3.3 阶段会替换为 LangGraph 真实驱动，
**对外签名（run_stream）保持稳定**，service 层无需感知内部变化。

接口契约：
    run_stream(session_id, user_input) -> AsyncIterator[AgentEvent]
        ↳ 异步生成器，逐条产出 AgentEvent。
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal


# ────────────── Agent 内部事件 ──────────────
# Agent 产出的中间事件结构。Service 层负责将其翻译为 SSE 事件。
# 之所以再设一层，是为了避免 Agent 直接依赖 HTTP/SSE 协议细节。


@dataclass
class AgentTextChunk:
    """文本块（一个或多个 token）。"""

    kind: Literal["text"] = "text"
    content: str = ""


@dataclass
class AgentToolStart:
    """工具开始执行。"""

    kind: Literal["tool_start"] = "tool_start"
    tool: str = ""
    args: dict | None = None


@dataclass
class AgentToolEnd:
    """工具执行结束。"""

    kind: Literal["tool_end"] = "tool_end"
    tool: str = ""
    output: str | None = None


@dataclass
class AgentDone:
    """运行完成。"""

    kind: Literal["done"] = "done"
    final_content: str = ""


AgentEvent = AgentTextChunk | AgentToolStart | AgentToolEnd | AgentDone


# ────────────── Mock 执行器 ──────────────


async def run_stream(
    session_id: uuid.UUID,
    user_input: str,
) -> AsyncIterator[AgentEvent]:
    """V1.0 mock 实现：模拟一段"思考 → 调用工具 → 回答"的完整链路。

    用于验证：
    - API-02 打字机式文本流
    - API-03 文本流 / 控制流的区分推送
    - AGT-02 多次进入工具节点（这里通过 1 次 tool 调用模拟）

    3.3 阶段将由 LangGraph 的真实 ReAct 循环替换。
    """
    # 1) 先吐一段"思考中"的开场文本
    intro = f"收到你的问题：「{user_input}」。让我先查一下知识库..."
    async for chunk in _stream_text(intro):
        yield AgentTextChunk(content=chunk)

    # 2) 模拟工具调用：tool_start → 工作 → tool_end
    yield AgentToolStart(tool="mock_search", args={"query": user_input[:32]})
    await asyncio.sleep(0.3)  # 模拟工具耗时
    yield AgentToolEnd(tool="mock_search", output="（已返回 3 条假设的知识切片）")

    # 3) 综合回答
    answer = "根据检索到的内容，这是一个 mock 的最终答复。3.3 阶段会接入真实 LangGraph ReAct 循环。"
    final_chunks: list[str] = []
    async for chunk in _stream_text(answer):
        final_chunks.append(chunk)
        yield AgentTextChunk(content=chunk)

    yield AgentDone(final_content=intro + answer)


async def _stream_text(text: str, chunk_size: int = 4, delay: float = 0.04) -> AsyncIterator[str]:
    """将文本按固定块大小切分后异步逐块吐出，模拟 token 流。"""
    for i in range(0, len(text), chunk_size):
        await asyncio.sleep(delay)
        yield text[i : i + chunk_size]