"""V2.0 T12 阶段单测（OBS-03 聚合统计）。"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ──────────────── ORM 模型 ────────────────


class TestQueryAnalyticsModel:
    """QueryAnalytics ORM 模型基础测试。"""

    def test_model_importable(self):
        from app.models.query_analytics import QueryAnalytics
        assert QueryAnalytics.__tablename__ == "query_analytics"

    def test_model_registered_in_init(self):
        from app.models import QueryAnalytics
        assert QueryAnalytics is not None

    def test_model_fields(self):
        from app.models.query_analytics import QueryAnalytics
        columns = {c.name for c in QueryAnalytics.__table__.columns}
        expected = {
            "id", "trace_id", "session_id", "kb_id",
            "total_latency_ms", "confidence", "low_confidence",
            "graph_rag_triggered", "bm25_contributed", "faithfulness_check_triggered",
            "total_tokens", "react_steps", "has_error", "created_at",
        }
        assert expected.issubset(columns), f"缺失列: {expected - columns}"


# ──────────────── Schema ────────────────


class TestAnalyticsSchema:
    """OBS-03 Analytics Schema 测试。"""

    def test_analytics_response_defaults(self):
        from app.schemas.v2.analytics import AnalyticsResponse
        resp = AnalyticsResponse()
        assert resp.total_queries == 0
        assert resp.low_confidence_rate == 0.0
        assert resp.error_rate == 0.0

    def test_analytics_response_with_data(self):
        from app.schemas.v2.analytics import AnalyticsResponse, ToolUsageStats, TokenConsumptionStats
        resp = AnalyticsResponse(
            total_queries=1520,
            avg_latency_ms=2840.5,
            avg_confidence=0.78,
            low_confidence_rate=0.12,
            tool_usage=ToolUsageStats(
                graph_rag_triggered=0.65,
                bm25_contributed=0.43,
                faithfulness_check_triggered=0.28,
            ),
            token_consumption=TokenConsumptionStats(total_tokens=5470000),
            avg_react_steps=3.2,
            error_rate=0.02,
            start_date=date(2026, 6, 9),
            end_date=date(2026, 6, 16),
        )
        assert resp.total_queries == 1520
        assert resp.tool_usage.graph_rag_triggered == 0.65
        assert resp.token_consumption.total_tokens == 5470000

    def test_tool_usage_stats_range(self):
        from app.schemas.v2.analytics import ToolUsageStats
        with pytest.raises(Exception):
            ToolUsageStats(graph_rag_triggered=1.5)  # 超出 [0, 1]
