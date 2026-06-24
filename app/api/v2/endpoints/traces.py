"""Trace 查询接口（OBS-02）。

端点：
- GET /api/v2/traces/{trace_id} — 返回单条 trace 完整步骤
- GET /api/v2/sessions/{session_id}/traces — 返回该会话所有 trace（分页）
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import error_codes
from app.api.deps import get_db
from app.api.exceptions import BusinessError
from app.models.agent_trace import AgentTrace
from app.schemas.v2.trace import (
    TraceDetail,
    TraceListItem,
    TraceListResponse,
    TraceStepItem,
)

router = APIRouter(prefix="/traces", tags=["V2 Trace 可观测性"])


@router.get("/{trace_id}", response_model=TraceDetail)
async def get_trace(
    trace_id: str,
    db: AsyncSession = Depends(get_db),
) -> TraceDetail:
    """获取单条 trace 的完整步骤链路。"""
    # 查所有步骤
    result = await db.execute(
        select(AgentTrace)
        .where(AgentTrace.trace_id == trace_id)
        .order_by(AgentTrace.created_at)
    )
    steps = result.scalars().all()

    if not steps:
        raise BusinessError(error_codes.NOT_FOUND, f"trace_id={trace_id} 不存在")

    # 从根步骤取 total_latency_ms
    root_steps = [s for s in steps if s.parent_step is None]
    total_latency = root_steps[0].total_latency_ms if root_steps else None
    session_id = steps[0].session_id
    kb_id = steps[0].kb_id
    created_at = steps[0].created_at

    return TraceDetail(
        trace_id=trace_id,
        session_id=session_id,
        kb_id=kb_id,
        total_latency_ms=total_latency,
        steps=[
            TraceStepItem(
                id=s.id,
                step_type=s.step_type,
                parent_step=s.parent_step,
                step_latency_ms=s.step_latency_ms,
                step_input=s.step_input,
                step_output=s.step_output,
                model_name=s.model_name,
                token_count=s.token_count,
                error_message=s.error_message,
                created_at=s.created_at,
            )
            for s in steps
        ],
        created_at=created_at,
    )


@router.get("/sessions/{session_id}/traces", response_model=TraceListResponse)
async def list_session_traces(
    session_id: uuid.UUID,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: AsyncSession = Depends(get_db),
) -> TraceListResponse:
    """获取某会话的所有 trace（分页）。"""
    # 总数
    count_result = await db.execute(
        select(func.count(func.distinct(AgentTrace.trace_id))).where(
            AgentTrace.session_id == session_id
        )
    )
    total = count_result.scalar() or 0

    # 分页查 trace_id 列表（取每个 trace 的根步骤）
    offset = (page - 1) * page_size
    result = await db.execute(
        select(AgentTrace)
        .where(
            AgentTrace.session_id == session_id,
            AgentTrace.parent_step.is_(None),  # 只取根步骤
        )
        .order_by(AgentTrace.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    root_steps = result.scalars().all()

    # B L-06：一次性 GROUP BY 取本页所有 trace 的 step_count，避免 N+1
    # 旧实现每个 trace 跑一次 count()，trace 多时是真实瓶颈
    trace_ids = [root.trace_id for root in root_steps]
    step_count_map: dict[str, int] = {}
    if trace_ids:
        count_result = await db.execute(
            select(AgentTrace.trace_id, func.count(AgentTrace.id))
            .where(AgentTrace.trace_id.in_(trace_ids))
            .group_by(AgentTrace.trace_id)
        )
        step_count_map = {row[0]: row[1] for row in count_result.all()}

    items = [
        TraceListItem(
            trace_id=root.trace_id,
            session_id=root.session_id,
            kb_id=root.kb_id,
            total_latency_ms=root.total_latency_ms,
            step_count=step_count_map.get(root.trace_id, 0),
            created_at=root.created_at,
        )
        for root in root_steps
    ]

    return TraceListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
