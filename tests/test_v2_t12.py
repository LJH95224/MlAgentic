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


# ──────────────── Analytics Writer ────────────────


class TestAnalyticsWriter:
    """快照写入辅助函数测试。"""

    def _make_tracer_steps(self):
        """构造模拟的 Tracer steps 列表。"""
        from app.observability.tracer import TraceStep
        return [
            TraceStep(step_type="query_rewrite", step_latency_ms=100, step_output={"rewritten_len": 50}),
            TraceStep(step_type="query_ner", step_latency_ms=200, step_output={"entity_count": 2}),
            TraceStep(step_type="graph_anchor", step_latency_ms=150, step_output={"tag_count": 3}),
            TraceStep(step_type="retrieve", step_latency_ms=300, step_input={"query_rewrite": "hyde"}, step_output={"hit_count": 5}),
            TraceStep(step_type="build_context", step_latency_ms=10, step_output={"chunks": 5}),
            TraceStep(step_type="generate", step_latency_ms=2000, model_name="deepseek-v4", token_count=1500, step_output={"answer_len": 200}),
            TraceStep(step_type="citation_parse", step_latency_ms=5, step_output={"citations": 3}),
            TraceStep(step_type="faithfulness_check", step_latency_ms=800, step_output={"status": "ok"}),
            TraceStep(step_type="compute_confidence", step_latency_ms=2, step_output={"confidence": 0.85}),
        ]

    def test_build_analytics_snapshot_normal(self):
        """正常查询的快照数据构建。"""
        from app.observability.analytics_writer import build_analytics_snapshot
        steps = self._make_tracer_steps()
        snap = build_analytics_snapshot(
            trace_id="abc123",
            session_id=uuid.uuid4(),
            kb_id=uuid.uuid4(),
            total_latency_ms=3500,
            confidence=0.85,
            enable_faithfulness_check=True,
            steps=steps,
        )
        assert snap["trace_id"] == "abc123"
        assert snap["total_latency_ms"] == 3500
        assert snap["confidence"] == 0.85
        assert snap["low_confidence"] is False  # 0.85 >= 0.5
        assert snap["graph_rag_triggered"] is True  # 有 graph_anchor 且 tag_count > 0
        assert snap["faithfulness_check_triggered"] is True  # 有 faithfulness_check 步骤
        assert snap["total_tokens"] == 1500  # generate 步骤的 token_count
        assert snap["react_steps"] == 9
        assert snap["has_error"] is False

    def test_build_analytics_snapshot_low_confidence(self):
        """低置信度场景。"""
        from app.observability.analytics_writer import build_analytics_snapshot
        steps = self._make_tracer_steps()
        snap = build_analytics_snapshot(
            trace_id="low1",
            session_id=None,
            kb_id=None,
            total_latency_ms=500,
            confidence=0.3,
            enable_faithfulness_check=False,
            steps=steps,
        )
        assert snap["low_confidence"] is True  # 0.3 < 0.5
        assert snap["faithfulness_check_triggered"] is False  # 开关关闭

    def test_build_analytics_snapshot_with_error(self):
        """有步骤出错的场景。"""
        from app.observability.analytics_writer import build_analytics_snapshot
        from app.observability.tracer import TraceStep
        steps = self._make_tracer_steps()
        steps.append(TraceStep(step_type="retrieve", error_message="Milvus timeout", step_latency_ms=5000))
        snap = build_analytics_snapshot(
            trace_id="err1",
            session_id=None,
            kb_id=None,
            total_latency_ms=6000,
            confidence=0.0,
            enable_faithfulness_check=False,
            steps=steps,
        )
        assert snap["has_error"] is True
        assert snap["low_confidence"] is True  # confidence=0.0 < 0.5

    def test_build_analytics_snapshot_empty_steps(self):
        """空步骤（检索为空兜底场景）。"""
        from app.observability.analytics_writer import build_analytics_snapshot
        snap = build_analytics_snapshot(
            trace_id="empty1",
            session_id=None,
            kb_id=None,
            total_latency_ms=100,
            confidence=0.0,
            enable_faithfulness_check=False,
            steps=[],
        )
        assert snap["react_steps"] == 0
        assert snap["total_tokens"] == 0
        assert snap["graph_rag_triggered"] is False
        assert snap["has_error"] is False
        assert snap["low_confidence"] is True  # 0.0 < 0.5
