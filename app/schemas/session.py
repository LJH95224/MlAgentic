"""会话相关的 Pydantic Schema（V1.0 + V1.5）。

V1.5 改造原则：
- 旧 `CreateSessionResponse` 保留为向后兼容别名（指向 SessionDetail），
  V1.0 的测试仍然能 `assert "id" in data`，不破坏
- 所有新 endpoint 全部走 V1.5 新 schema：
  - `SessionCreateRequest` / `SessionUpdateRequest`（请求）
  - `SessionDetail` / `SessionListItem` / `SessionListResponse`（响应）
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ──────────────── 请求 ────────────────


class SessionCreateRequest(BaseModel):
    """POST /api/v1/sessions 请求体（V1.5 SES-01）。

    `title` 可选；不传则由后续 SES-07 异步任务生成。
    """

    title: str | None = Field(
        None,
        max_length=100,
        description="可选的会话标题；不传则首条对话后自动生成",
    )

    # PRD SES-04 强调 title 非空：若显式传了 title，不允许空白串
    @field_validator("title")
    @classmethod
    def _no_blank_title(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("title 不能为空白字符串；若不想设置请整体省略此字段")
        return v


class SessionUpdateRequest(BaseModel):
    """PATCH /api/v1/sessions/{sid} 请求体（V1.5 SES-04）。

    仅 `title` 字段可手动修改（PRD 明确：其余元数据由系统维护）。
    `extra="forbid"` 会让传入其他字段（如试图改 summary / message_count）
    直接被 Pydantic 拦截 → 统一翻译成 40001 PARAM_INVALID。
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="新的会话标题（1~100 字）",
    )

    @field_validator("title")
    @classmethod
    def _no_blank_title(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title 不能为空白字符串")
        return v


# ──────────────── 响应 ────────────────


class SessionDetail(BaseModel):
    """会话详情（V1.5 SES-01 / SES-03 共用）。

    包含全部用户可见字段；其中 `summary` / `summarized_at` 在 SES-08 触发摘要前为 null。
    """

    id: uuid.UUID = Field(..., description="会话唯一标识")
    title: str | None = Field(None, description="会话标题（未生成时为 null）")
    summary: str | None = Field(None, description="会话摘要")
    summarized_at: datetime | None = Field(None, description="摘要生成时间")
    message_count: int = Field(0, description="消息总数（冗余统计字段）")
    metadata: dict | None = Field(
        None,
        description="预留元数据（会话偏好、地理范围等）",
    )
    created_at: datetime = Field(..., description="创建时间（UTC）")
    updated_at: datetime = Field(..., description="最近一次消息写入时间")

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_orm_session(cls, session) -> "SessionDetail":
        """显式适配器：ORM 模型用 `db_metadata` 列规避 SQLAlchemy 保留字。"""
        return cls(
            id=session.id,
            title=session.title,
            summary=session.summary,
            summarized_at=session.summarized_at,
            message_count=session.message_count,
            metadata=session.db_metadata,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )


class SessionListItem(BaseModel):
    """会话列表项（V1.5 SES-02）。

    PRD 明确：列表项含 id/title/summary_snippet(前80字)/message_count/updated_at；
    `summary` 全文不在列表中返回（性能考虑），需走 SES-03 详情接口。
    """

    id: uuid.UUID
    title: str | None
    summary_snippet: str | None = Field(
        None, description="摘要前 80 字截断；摘要未生成时为 null"
    )
    message_count: int
    updated_at: datetime

    @classmethod
    def from_orm_session(cls, session) -> "SessionListItem":
        snippet = session.summary[:80] if session.summary else None
        return cls(
            id=session.id,
            title=session.title,
            summary_snippet=snippet,
            message_count=session.message_count,
            updated_at=session.updated_at,
        )


class SessionListResponse(BaseModel):
    """会话列表分页响应（V1.5 SES-02）。"""

    items: list[SessionListItem]
    page: int = Field(..., ge=1, description="当前页码（从 1 起）")
    page_size: int = Field(..., ge=1, le=100, description="每页条数")
    total: int = Field(..., ge=0, description="总数")


# ──────────────── 向后兼容（V1.0 别名） ────────────────


# V1.0 时的 `CreateSessionResponse{id, created_at}` 仍被外部代码 import，
# 这里保留同名导出，但底层换成 SessionDetail（多字段，老测试 `"id" in data`
# 仍然成立）。S2 阶段如确认无外部引用可删除此别名。
CreateSessionResponse = SessionDetail


# ──────────────── 兼容旧导出 ────────────────


class SessionInfo(BaseModel):
    """会话基础信息（仅 V1.0 内部脚本可能引用，保留避免破坏）。"""

    id: uuid.UUID
    created_at: datetime
    db_metadata: dict | None = Field(None, alias="metadata")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
