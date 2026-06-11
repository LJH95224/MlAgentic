"""对话服务：调度 Agent + 持久化消息 + 产出 SSE 事件。

设计上，本模块是 API 层与 Agent 层之间的"翻译官"：
- 入口：接收 (db, session_id, user_input)
- 出口：异步迭代器，产出已经 dump 成 dict 的 SSE 事件
- 副作用：持久化用户消息与 assistant 最终回复 + 维护会话冗余统计

V1.5 改造（SES-09 + S1.2/S1.3）：
- _load_history 按 settings.context_window_messages 截断，system 必含、不计数
- 写每条消息（user / assistant / tool）时同步维护 ChatSession.message_count 与 updated_at
- 写消息封装到 _append_message 中，避免散落 ChatMessage(...) + add 调用
"""

import logging
import uuid
from collections.abc import AsyncIterator

from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import runner as agent_runner
from app.agent.runner import (
    AgentDone,
    AgentTextChunk,
    AgentToolEnd,
    AgentToolStart,
)
from app.core.config import get_settings
from app.models.message import ChatMessage
from app.models.session import ChatSession
from app.schemas.chat import (
    DoneEvent,
    SSEEvent,
    TextEvent,
    ToolEndEvent,
    ToolStartEvent,
)

logger = logging.getLogger(__name__)


# ──────────────── 历史消息加载（SES-09） ────────────────


async def _load_history(db: AsyncSession, session_id: uuid.UUID) -> list[dict]:
    """从 DB 加载该 session 的历史消息（仅供 Agent 推理用），转为 OpenAI dict 格式。

    V1.5 SES-09 上下文窗口策略（与 PRD §3.1 SES-09 一致）：
    - 仅取最近 `CONTEXT_WINDOW_MESSAGES` 条非 system 消息进入推理
    - system 消息始终包含、不计入窗口（满足 PRD 中"system 始终包含、不计入 N 的限制"）
    - tool 类型计入窗口计数（PRD 明确）
    - 超出窗口的历史消息持久化保留，但本轮推理不可见 —— 通过 SES-06 接口仍可完整访问

    返回值示例：
        [
            {"role": "system", "content": "..."},          # 始终包含
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
            ...
        ]

    注意：tool / assistant-with-tool_calls 消息也会原样返回，让 Agent 能
    理解过去的工具调用链路。
    """
    window = max(get_settings().context_window_messages, 1)

    # 1. 拿全部 system 消息（不计数；按 created_at 正序）
    # 加 id 做 tie-breaker：created_at 在 server_default=func.now() 同事务多 insert
    # 时可能完全相同，PG 在 tie 下不保证按插入顺序排序
    sys_stmt = (
        select(ChatMessage)
        .where(
            ChatMessage.session_id == session_id,
            ChatMessage.role == "system",
        )
        .order_by(ChatMessage.created_at, ChatMessage.id)
    )
    sys_rows = list((await db.execute(sys_stmt)).scalars().all())

    # 2. 拿最近 N 条非 system（按 created_at 倒序取，再反转得正序）
    non_sys_stmt = (
        select(ChatMessage)
        .where(
            ChatMessage.session_id == session_id,
            ChatMessage.role != "system",
        )
        .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
        .limit(window)
    )
    non_sys_rows = list((await db.execute(non_sys_stmt)).scalars().all())
    non_sys_rows.reverse()  # 正序：旧 → 新

    rows = sys_rows + non_sys_rows
    total_in_db = await _count_messages(db, session_id)
    if total_in_db > len(rows):
        logger.info(
            "SES-09 上下文窗口截断: session=%s 总=%d 入推理=%d（system %d + 最近 %d）",
            session_id,
            total_in_db,
            len(rows),
            len(sys_rows),
            len(non_sys_rows),
        )

    history: list[dict] = []
    for m in rows:
        item: dict = {"role": m.role, "content": m.content}
        if m.tool_calls:
            item["tool_calls"] = m.tool_calls
        history.append(item)
    return history


async def _count_messages(db: AsyncSession, session_id: uuid.UUID) -> int:
    """统计 session 的消息总数；用于日志区分"加载多少条 vs 总共多少条"。"""
    stmt = (
        select(func.count())
        .select_from(ChatMessage)
        .where(ChatMessage.session_id == session_id)
    )
    return int((await db.execute(stmt)).scalar_one() or 0)


# ──────────────── 消息写入 + 会话冗余统计维护 ────────────────


async def _append_message(
    db: AsyncSession,
    session_id: uuid.UUID,
    *,
    role: str,
    content: str | None,
    tool_calls: list | None = None,
) -> ChatMessage:
    """写一条消息 + 维护会话冗余统计（message_count + updated_at）。

    V1.5：
    - message_count 是冗余统计字段（PRD §5.1），写消息时 +1
    - updated_at 由 ChatSession 的 onupdate 触发，但 onupdate 仅在该行被 UPDATE 时生效
      （单纯 insert ChatMessage 不会动 session 行）；这里用一条 UPDATE 显式 touch，
      让 SES-02 列表能按 updated_at DESC 正确排序
    """
    msg = ChatMessage(
        session_id=session_id,
        role=role,
        content=content,
        tool_calls=tool_calls,
    )
    db.add(msg)

    # 同一事务里 +1 + touch updated_at；不读再回写，避免并发覆盖
    await db.execute(
        update(ChatSession)
        .where(ChatSession.id == session_id)
        .values(
            message_count=ChatSession.message_count + 1,
            updated_at=func.now(),
        )
    )

    await db.commit()
    await db.refresh(msg)
    return msg


# ──────────────── 对外主入口 ────────────────


async def stream_chat(
    db: AsyncSession,
    session_id: uuid.UUID,
    user_input: str,
) -> AsyncIterator[SSEEvent]:
    """处理一次完整的对话请求并以 SSE 事件形式流式返回。

    步骤：
    1. 从 DB 加载历史消息（按 SES-09 上下文窗口截断；本轮 user_input 尚未落库）
    2. 持久化本轮用户消息 + 维护会话计数
    3. 调用 agent runner，传入历史 + 当前输入
    4. 把 Agent 内部事件翻译为对外 SSE 事件并 yield
    5. 持久化 assistant 最终回复 + 维护会话计数
    6. 最后发送 done 事件
    """
    # 1. 先取历史（在 user_input 落库之前），避免本轮 user 自己也被算进上下文
    history = await _load_history(db, session_id)
    logger.info("加载历史消息: session=%s into_context=%d", session_id, len(history))

    # 2. 用户消息落库 + 维护 message_count / updated_at
    await _append_message(db, session_id, role="user", content=user_input)

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
            await _append_message(
                db, session_id, role="assistant", content=final_text
            )
            yield DoneEvent()
