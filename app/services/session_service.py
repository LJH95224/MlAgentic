"""会话相关的业务逻辑（V1.0 API-01 + V1.5 SES-01~06）。

设计原则：
- service 层只做 DB 操作 + 业务规则校验；不构造 ApiResponse、不调 HTTPException
- 不存在 / 名称冲突等业务失败 → 抛 BusinessError（统一 handler 翻译为 ApiResponse）
- service 函数签名稳定后，后续 S4 会在 chat_service 的"首轮 AI 回复完成"钩子里
  调 update_session_after_message 等方法
"""

import uuid

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import error_codes
from app.api.exceptions import BusinessError
from app.models.message import ChatMessage
from app.models.session import ChatSession

# 列表分页上限（PRD SES-02 默认 20，硬上限 100 防滥用）
MAX_PAGE_SIZE = 100
# 消息游标翻页上限（PRD SES-06 默认 20，硬上限 100）
MAX_MESSAGE_LIMIT = 100


# ──────────────── 创建（SES-01） ────────────────


async def create_session(
    db: AsyncSession, title: str | None = None
) -> ChatSession:
    """创建一个新会话并落库。

    V1.0：无 title 参数；V1.5 起支持可选 title。
    """
    session = ChatSession(title=title)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


# ──────────────── 查询 ────────────────


async def get_session(
    db: AsyncSession, session_id: uuid.UUID
) -> ChatSession | None:
    """按 ID 获取会话；不存在返回 None。

    V1.0 至今的契约：上层（chat endpoint）按 None 自行处理 404。
    """
    stmt = select(ChatSession).where(ChatSession.id == session_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_session_or_raise(
    db: AsyncSession, session_id: uuid.UUID
) -> ChatSession:
    """按 ID 获取会话；不存在抛 BusinessError(NOT_FOUND)。

    新 V1.5 endpoint 一律走这个，让 BusinessError handler 出统一 404 响应。
    """
    session = await get_session(db, session_id)
    if session is None:
        raise BusinessError(
            error_codes.NOT_FOUND, f"会话 {session_id} 不存在"
        )
    return session


# ──────────────── 列表 + 总数（SES-02） ────────────────


async def list_sessions(
    db: AsyncSession, page: int = 1, page_size: int = 20
) -> tuple[list[ChatSession], int]:
    """分页返回会话列表 + 总数；按 updated_at 倒序。

    返回 (items, total)；上层组装 SessionListResponse。
    """
    page = max(page, 1)
    page_size = max(min(page_size, MAX_PAGE_SIZE), 1)

    # 总数
    total_stmt = select(func.count()).select_from(ChatSession)
    total = (await db.execute(total_stmt)).scalar_one()

    # 列表（updated_at 倒序）
    items_stmt = (
        select(ChatSession)
        .order_by(desc(ChatSession.updated_at), desc(ChatSession.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = list((await db.execute(items_stmt)).scalars().all())

    return items, total


# ──────────────── 更新标题（SES-04） ────────────────


async def update_session_title(
    db: AsyncSession, session_id: uuid.UUID, title: str
) -> ChatSession:
    """更新会话标题；title 为 None / 空白由 Pydantic 层拦截。"""
    session = await get_session_or_raise(db, session_id)
    session.title = title
    await db.commit()
    await db.refresh(session)
    return session


# ──────────────── 删除（SES-05） ────────────────


async def delete_session(db: AsyncSession, session_id: uuid.UUID) -> None:
    """物理删除会话；关联消息由 ORM cascade="all, delete-orphan" 自动级联。"""
    session = await get_session_or_raise(db, session_id)
    await db.delete(session)
    await db.commit()


# ──────────────── 消息历史（SES-06） ────────────────


async def list_session_messages(
    db: AsyncSession,
    session_id: uuid.UUID,
    limit: int = 20,
    before: uuid.UUID | None = None,
) -> tuple[list[ChatMessage], bool, uuid.UUID | None]:
    """按游标翻页返回会话的消息列表，正序排列。

    语义：
    - 不传 before → 取最早的 N 条
    - 传 before → 取该消息之前（更早）的 N 条

    返回 (items, has_more, next_before)：
    - items: 按 created_at 正序的消息
    - has_more: 是否还有更早的消息
    - next_before: 前端下次翻页用的游标 = items 首条 id

    实现细节：
    - 先验证会话存在（SES-06 验收要求 404 路径）
    - 用子查询拿到 before 消息的 created_at，避免两次往返
    - 多取一条判断 has_more；返回时丢掉这条
    """
    await get_session_or_raise(db, session_id)
    limit = max(min(limit, MAX_MESSAGE_LIMIT), 1)

    base = select(ChatMessage).where(ChatMessage.session_id == session_id)

    if before is not None:
        # 用子查询定位 before 消息的 created_at
        before_ts_stmt = select(ChatMessage.created_at).where(
            ChatMessage.id == before, ChatMessage.session_id == session_id
        )
        before_ts = (await db.execute(before_ts_stmt)).scalar_one_or_none()
        if before_ts is None:
            # before 消息不存在或不属于该会话 → 当作非法游标
            raise BusinessError(
                error_codes.PARAM_INVALID,
                f"游标消息 {before} 不存在或不属于会话 {session_id}",
            )
        base = base.where(ChatMessage.created_at < before_ts)

    # 取 limit+1 用于 has_more 判定
    # 加 id 做 tie-breaker：批量 insert 的 server_default=func.now() 可能 tie，
    # 单靠 created_at 排序不确定，会让翻页"看似有序但实际重复 / 漏"
    stmt = base.order_by(
        desc(ChatMessage.created_at), desc(ChatMessage.id)
    ).limit(limit + 1)
    rows = list((await db.execute(stmt)).scalars().all())

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    # 按 PRD 要求正序返回（rows 当前是按 created_at desc）
    rows.reverse()

    next_before = rows[0].id if rows else None
    return rows, has_more, next_before
