"""对话服务：调度 Agent + 持久化消息 + 产出 SSE 事件。

设计上，本模块是 API 层与 Agent 层之间的"翻译官"：
- 入口：接收 (db, session_id, user_input)
- 出口：异步迭代器，产出已经 dump 成 dict 的 SSE 事件
- 副作用：持久化用户消息与 assistant 最终回复
"""

import logging
import uuid
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import runner as agent_runner
from app.agent.runner import (
    AgentDone,
    AgentTextChunk,
    AgentToolEnd,
    AgentToolStart,
)
from app.models.message import ChatMessage
from app.schemas.chat import (
    DoneEvent,
    SSEEvent,
    TextEvent,
    ToolEndEvent,
    ToolStartEvent,
)

logger = logging.getLogger(__name__)


async def _load_history(db: AsyncSession, session_id: uuid.UUID) -> list[dict]:
    """从 DB 加载该 session 的历史消息，转为 OpenAI dict 格式。

    返回值示例：
        [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮你的？"},
            ...
        ]

    注意：tool / assistant-with-tool_calls 消息也会原样返回，让 Agent 能
    理解过去的工具调用链路。
    """
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    )
    rows = (await db.execute(stmt)).scalars().all()

    history: list[dict] = []
    for m in rows:
        item: dict = {"role": m.role, "content": m.content}
        if m.tool_calls:
            item["tool_calls"] = m.tool_calls
        history.append(item)
    return history


async def stream_chat(
    db: AsyncSession,
    session_id: uuid.UUID,
    user_input: str,
) -> AsyncIterator[SSEEvent]:
    """处理一次完整的对话请求并以 SSE 事件形式流式返回。

    步骤：
    1. 从 DB 加载历史消息（不含本轮 user_input）
    2. 持久化本轮用户消息
    3. 调用 agent runner，传入历史 + 当前输入
    4. 把 Agent 内部事件翻译为对外 SSE 事件并 yield
    5. 持久化 assistant 最终回复
    6. 最后发送 done 事件
    """
    # 1. 先取历史（在 user_input 落库之前），避免重复
    history = await _load_history(db, session_id)
    logger.info("加载历史消息: session=%s count=%d", session_id, len(history))

    # 2. 用户消息落库
    user_msg = ChatMessage(session_id=session_id, role="user", content=user_input)
    db.add(user_msg)
    await db.commit()

    # 3. 调度 Agent，逐事件翻译为 SSE 事件
    final_text_parts: list[str] = []
    async for event in agent_runner.run_stream(session_id, user_input, history=history):
        if isinstance(event, AgentTextChunk):
            final_text_parts.append(event.content)
            yield TextEvent(content=event.content)
        elif isinstance(event, AgentToolStart):
            yield ToolStartEvent(tool=event.tool, args=event.args)
        elif isinstance(event, AgentToolEnd):
            yield ToolEndEvent(tool=event.tool, output=event.output)
        elif isinstance(event, AgentDone):
            # 优先采用 runner 给出的 final_content，否则回退到累积的 text
            final_text = event.final_content or "".join(final_text_parts)
            assistant_msg = ChatMessage(
                session_id=session_id,
                role="assistant",
                content=final_text,
            )
            db.add(assistant_msg)
            await db.commit()
            yield DoneEvent()
