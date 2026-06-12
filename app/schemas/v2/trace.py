"""V2.0 Trace 相关 Schema（OBS-01/02）。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class TraceStepItem(BaseModel):
    """单条 trace 步骤。"""

    id: uuid.UUID
    step_type: str
    parent_step: str | None = None
    step_latency_ms: int | None = None
    step_input: dict | None = None
    step_output: dict | None = None
    model_name: str | None = None
    token_count: int | None = None
    error_message: str | None = None
    created_at: datetime


class TraceDetail(BaseModel):
    """一次 trace 的完整信息（含所有步骤）。"""

    trace_id: str
    session_id: uuid.UUID | None = None
    kb_id: uuid.UUID | None = None
    total_latency_ms: int | None = None
    steps: list[TraceStepItem]
    created_at: datetime | None = None


class TraceListItem(BaseModel):
    """trace 列表中的条目（不含步骤详情）。"""

    trace_id: str
    session_id: uuid.UUID | None = None
    kb_id: uuid.UUID | None = None
    total_latency_ms: int | None = None
    step_count: int = 0
    created_at: datetime | None = None


class TraceListResponse(BaseModel):
    """trace 列表响应。"""

    items: list[TraceListItem]
    total: int
    page: int = 1
    page_size: int = 20
