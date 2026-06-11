"""会话消息相关的 Pydantic Schema（V1.5 SES-06）。"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# 消息角色（与 ChatMessage.role 约束一致）
MessageRole = Literal["system", "user", "assistant", "tool"]


class MessageItem(BaseModel):
    """单条消息（V1.5 SES-06）。

    `tool_calls` 仅在 assistant 角色发起 function calling 时非空；
    `content` 仅在 tool 角色返回结果时可能为空（PRD §4.2 约定）。
    """

    id: uuid.UUID
    role: MessageRole
    content: str | None
    tool_calls: list | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MessageListResponse(BaseModel):
    """消息游标翻页响应（V1.5 SES-06）。

    PRD 设计语义："返回 before 消息之前的、按 created_at 正序的 N 条"
    - 不传 before → 拉最早的 N 条
    - 传 before → 拉该消息之前的 N 条

    `has_more` 不是 PRD 必需，但前端做"加载更多历史"按钮的可见性
    时必须知道，所以加上；不影响 PRD 验收。
    """

    items: list[MessageItem] = Field(..., description="消息列表，按 created_at 正序")
    has_more: bool = Field(
        ..., description="是否还有更早的消息（前端用此显示『加载更多』按钮）"
    )
    next_before: uuid.UUID | None = Field(
        None,
        description="向前翻页用的游标 = items 首条 id；items 为空时为 null",
    )
