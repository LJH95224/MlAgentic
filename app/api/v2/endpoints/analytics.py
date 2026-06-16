"""V2.0 OBS-03 聚合统计端点 GET /api/v2/analytics。

从 query_analytics 快照表做 SQL 聚合，返回系统级统计数据。
支持按时间范围和知识库过滤。单次 SQL 查询完成所有聚合。
"""

from __future__ import annotations

import logging
import uuid as _uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models.query_analytics import QueryAnalytics
from app.schemas.v2.analytics import (
    AnalyticsResponse,
    TokenConsumptionStats,
    ToolUsageStats,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["V2 可观测性"])


@router.get("/analytics", response_model=AnalyticsResponse)
async def v2_analytics(
    start_date: date | None = Query(default=None, description="统计开始日期（默认 7 天前）"),
    end_date: date | None = Query(default=None, description="统计结束日期（默认今天）"),
    kb_id: str | None = Query(default=None, description="按知识库过滤"),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsResponse:
    """OBS-03 聚合统计：查询量 / 延迟 / 置信度 / 工具使用率 / Token / 错误率。"""
    # 默认时间范围：最近 7 天
    if end_date is None:
        end_date = date.today()
    if start_date is None:
        start_date = end_date - timedelta(days=7)

    # 转 datetime 做 PG 比较
    start_dt = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
    # end_date 的次日 0 点（左闭右开）
    end_dt = datetime(end_date.year, end_date.month, end_date.day, tzinfo=timezone.utc) + timedelta(days=1)

    # 构建聚合查询——单次 SQL 完成所有聚合
    stmt = select(
        func.count(QueryAnalytics.id).label("total_queries"),
        func.avg(QueryAnalytics.total_latency_ms).label("avg_latency_ms"),
        func.avg(QueryAnalytics.confidence).label("avg_confidence"),
        func.avg(
            case((QueryAnalytics.low_confidence == True, 1.0), else_=0.0)  # noqa: E712
        ).label("low_confidence_rate"),
        func.avg(
            case((QueryAnalytics.graph_rag_triggered == True, 1.0), else_=0.0)  # noqa: E712
        ).label("graph_rag_triggered_rate"),
        func.avg(
            case((QueryAnalytics.bm25_contributed == True, 1.0), else_=0.0)  # noqa: E712
        ).label("bm25_contributed_rate"),
        func.avg(
            case((QueryAnalytics.faithfulness_check_triggered == True, 1.0), else_=0.0)  # noqa: E712
        ).label("faithfulness_check_rate"),
        func.sum(QueryAnalytics.total_tokens).label("total_tokens"),
        func.avg(QueryAnalytics.react_steps).label("avg_react_steps"),
        func.avg(
            case((QueryAnalytics.has_error == True, 1.0), else_=0.0)  # noqa: E712
        ).label("error_rate"),
    ).where(
        QueryAnalytics.created_at >= start_dt,
        QueryAnalytics.created_at < end_dt,
    )

    # kb_id 过滤
    if kb_id is not None:
        try:
            kb_uuid = _uuid.UUID(kb_id)
        except ValueError:
            kb_uuid = None
        if kb_uuid is not None:
            stmt = stmt.where(QueryAnalytics.kb_id == kb_uuid)

    result = await db.execute(stmt)
    row = result.first()

    if row is None or row.total_queries == 0:
        return AnalyticsResponse(
            total_queries=0,
            start_date=start_date,
            end_date=end_date,
        )

    return AnalyticsResponse(
        total_queries=row.total_queries,
        avg_latency_ms=round(row.avg_latency_ms, 1) if row.avg_latency_ms else None,
        avg_confidence=round(row.avg_confidence, 4) if row.avg_confidence else None,
        low_confidence_rate=round(row.low_confidence_rate, 4) if row.low_confidence_rate else 0.0,
        tool_usage=ToolUsageStats(
            graph_rag_triggered=round(row.graph_rag_triggered_rate, 4) if row.graph_rag_triggered_rate else 0.0,
            bm25_contributed=round(row.bm25_contributed_rate, 4) if row.bm25_contributed_rate else 0.0,
            faithfulness_check_triggered=round(row.faithfulness_check_rate, 4) if row.faithfulness_check_rate else 0.0,
        ),
        token_consumption=TokenConsumptionStats(
            total_tokens=row.total_tokens or 0,
        ),
        avg_react_steps=round(row.avg_react_steps, 2) if row.avg_react_steps else None,
        error_rate=round(row.error_rate, 4) if row.error_rate else 0.0,
        start_date=start_date,
        end_date=end_date,
    )
