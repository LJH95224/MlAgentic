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
            TraceStep(step_type="retrieve", step_latency_ms=300, step_input={"query_rewrite": "hyde"}, step_output={"hit_count": 5, "bm25_enabled": True}),
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
        assert snap["bm25_contributed"] is True  # retrieve 开 bm25 + 有命中
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
        assert snap["bm25_contributed"] is False  # 没 retrieve step → 不算贡献
        assert snap["has_error"] is False
        assert snap["low_confidence"] is True  # 0.0 < 0.5

    def test_bm25_not_contributed_when_disabled(self):
        """B-M-11：bm25_enable=False 时不算贡献，哪怕有命中。"""
        from app.observability.analytics_writer import build_analytics_snapshot
        from app.observability.tracer import TraceStep
        steps = [
            TraceStep(
                step_type="retrieve",
                step_latency_ms=100,
                step_output={"hit_count": 5, "bm25_enabled": False},
            ),
        ]
        snap = build_analytics_snapshot(
            trace_id="bm25_off",
            session_id=None,
            kb_id=None,
            total_latency_ms=100,
            confidence=0.8,
            enable_faithfulness_check=False,
            steps=steps,
        )
        assert snap["bm25_contributed"] is False

    def test_bm25_not_contributed_when_no_hits(self):
        """B-M-11：bm25 开启但检索 0 命中，不算贡献（无数据可融合）。"""
        from app.observability.analytics_writer import build_analytics_snapshot
        from app.observability.tracer import TraceStep
        steps = [
            TraceStep(
                step_type="retrieve",
                step_latency_ms=100,
                step_output={"hit_count": 0, "bm25_enabled": True},
            ),
        ]
        snap = build_analytics_snapshot(
            trace_id="empty_hit",
            session_id=None,
            kb_id=None,
            total_latency_ms=100,
            confidence=0.0,
            enable_faithfulness_check=False,
            steps=steps,
        )
        assert snap["bm25_contributed"] is False

    def test_bm25_legacy_step_without_flag(self):
        """旧 trace 数据缺 bm25_enabled 字段时，保守判定为未贡献（避免误统计）。"""
        from app.observability.analytics_writer import build_analytics_snapshot
        from app.observability.tracer import TraceStep
        steps = [
            TraceStep(
                step_type="retrieve",
                step_latency_ms=100,
                step_output={"hit_count": 5},  # 旧字段，无 bm25_enabled
            ),
        ]
        snap = build_analytics_snapshot(
            trace_id="legacy",
            session_id=None,
            kb_id=None,
            total_latency_ms=100,
            confidence=0.8,
            enable_faithfulness_check=False,
            steps=steps,
        )
        # 缺字段时 .get() 返 None，is True 不成立 → 不算贡献
        assert snap["bm25_contributed"] is False


# ──────────────── A P2-19：rollback 失败必须留日志 ────────────────


class TestAnalyticsWriterRollbackFailure:
    """A P2-19：write_analytics_snapshot 在 rollback 也失败时不能裸吞。

    原行为：commit 失败 → rollback 也失败 → except: pass，无任何痕迹。
    修复后：rollback 失败必须 logger.warning，便于排查 session 半损坏的连锁问题。
    """

    @pytest.mark.asyncio
    async def test_rollback_failure_logs_warning(self, caplog):
        """commit 抛 + rollback 也抛时，两条 warning 都要出来。"""
        import logging
        from app.observability.analytics_writer import write_analytics_snapshot

        mock_db = MagicMock()
        # add 是同步调用
        mock_db.add = MagicMock()
        # commit / rollback 都抛
        mock_db.commit = AsyncMock(side_effect=RuntimeError("commit boom"))
        mock_db.rollback = AsyncMock(side_effect=RuntimeError("rollback boom"))

        with caplog.at_level(logging.WARNING, logger="app.observability.analytics_writer"):
            await write_analytics_snapshot(
                db=mock_db,
                trace_id="t-1",
                session_id=None,
                kb_id=None,
                total_latency_ms=10,
                confidence=0.5,
                enable_faithfulness_check=False,
                steps=[],
            )

        # 主路径 commit 失败的 warning 必有
        assert any("快照写入失败" in r.message for r in caplog.records), (
            "commit 失败应记录 warning"
        )
        # A P2-19：rollback 失败的 warning 也必有，不能裸吞
        assert any("rollback" in r.message.lower() for r in caplog.records), (
            "rollback 失败必须留日志，避免静默吞异常"
        )
        # 验证两个调用都发生过
        mock_db.commit.assert_awaited_once()
        mock_db.rollback.assert_awaited_once()


# ──────────────── /v2/query 集成 ────────────────


class TestQueryAnalyticsIntegration:
    """验证 /v2/query 末尾正确调用 write_analytics_snapshot。"""

    @pytest.mark.asyncio
    async def test_query_writes_analytics_on_success(self):
        """正常查询完成后写一行 analytics 快照。"""
        from app.api.v2.endpoints.query import v2_query
        from app.schemas.v2.query import QueryRequest

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=None)

        with patch("app.api.v2.endpoints.query.hybrid_search", new_callable=AsyncMock) as mock_search, \
             patch("app.api.v2.endpoints.query.generate_answer", new_callable=AsyncMock) as mock_gen, \
             patch("app.api.v2.endpoints.query.write_analytics_snapshot", new_callable=AsyncMock) as mock_write:

            mock_search.return_value = [
                MagicMock(chunk_id=1, content="内容", document_id="d1", score=0.9,
                          entity_tags=[], heading_path=[], block_type="paragraph",
                          page_number=1, metadata={}, source_collection="kb"),
            ]
            mock_gen.return_value = "答案[1]。"

            body = QueryRequest(query="测试", kb_ids=[uuid.uuid4()])
            resp = await v2_query(body=body, db=mock_db)
            mock_write.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_writes_analytics_on_empty_results(self):
        """检索为空兜底分支也写 analytics 快照。"""
        from app.api.v2.endpoints.query import v2_query
        from app.schemas.v2.query import QueryRequest

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=None)

        with patch("app.api.v2.endpoints.query.hybrid_search", new_callable=AsyncMock) as mock_search, \
             patch("app.api.v2.endpoints.query.write_analytics_snapshot", new_callable=AsyncMock) as mock_write:

            mock_search.return_value = []

            body = QueryRequest(query="不存在的查询", kb_ids=[uuid.uuid4()])
            resp = await v2_query(body=body, db=mock_db)
            mock_write.assert_called_once()
            # 检索空场景 confidence=0.0
            call_kwargs = mock_write.call_args[1]
            assert call_kwargs["confidence"] == 0.0


# ──────────────── Analytics 端点 ────────────────


class TestAnalyticsEndpoint:
    """OBS-03 GET /api/v2/analytics 端点测试。"""

    @pytest.mark.asyncio
    async def test_analytics_returns_stats(self):
        """正常返回聚合统计数据。"""
        from app.api.v2.endpoints.analytics import _build_analytics_response

        mock_db = MagicMock()
        mock_row = MagicMock()
        mock_row.total_queries = 100
        mock_row.avg_latency_ms = 2500.0
        mock_row.avg_confidence = 0.78
        mock_row.low_confidence_rate = 0.12
        mock_row.graph_rag_triggered_rate = 0.65
        mock_row.bm25_contributed_rate = 0.43
        mock_row.faithfulness_check_rate = 0.28
        mock_row.total_tokens = 500000
        mock_row.avg_react_steps = 3.2
        mock_row.error_rate = 0.02

        mock_result = MagicMock()
        mock_result.first.return_value = mock_row
        mock_db.execute = AsyncMock(return_value=mock_result)

        resp = await _build_analytics_response(
            start_date=date(2026, 6, 9),
            end_date=date(2026, 6, 16),
            kb_id=None,
            db=mock_db,
        )
        assert resp.total_queries == 100
        assert resp.avg_latency_ms == 2500.0
        assert resp.tool_usage.graph_rag_triggered == 0.65

    @pytest.mark.asyncio
    async def test_analytics_empty_data(self):
        """无数据时返回零值默认。"""
        from app.api.v2.endpoints.analytics import _build_analytics_response

        mock_db = MagicMock()
        mock_row = MagicMock()
        mock_row.total_queries = 0
        mock_row.avg_latency_ms = None
        mock_row.avg_confidence = None
        mock_row.low_confidence_rate = 0.0
        mock_row.graph_rag_triggered_rate = 0.0
        mock_row.bm25_contributed_rate = 0.0
        mock_row.faithfulness_check_rate = 0.0
        mock_row.total_tokens = 0
        mock_row.avg_react_steps = None
        mock_row.error_rate = 0.0

        mock_result = MagicMock()
        mock_result.first.return_value = mock_row
        mock_db.execute = AsyncMock(return_value=mock_result)

        resp = await _build_analytics_response(
            start_date=date(2026, 6, 9),
            end_date=date(2026, 6, 16),
            kb_id=None,
            db=mock_db,
        )
        assert resp.total_queries == 0
        assert resp.avg_latency_ms is None

    @pytest.mark.asyncio
    async def test_analytics_endpoint_wraps_api_response(self):
        """HTTP endpoint 返回 ApiResponse 包装，符合 V2 REST 统一响应契约。"""
        from app.api.v2.endpoints.analytics import v2_analytics

        mock_db = MagicMock()
        mock_row = MagicMock()
        mock_row.total_queries = 0
        mock_result = MagicMock()
        mock_result.first.return_value = mock_row
        mock_db.execute = AsyncMock(return_value=mock_result)

        resp = await v2_analytics(
            start_date=date(2026, 6, 9),
            end_date=date(2026, 6, 16),
            kb_id=None,
            db=mock_db,
        )
        assert resp.code == 0
        assert resp.message == "success"
        assert resp.data is not None
        assert resp.data.total_queries == 0

    def test_analytics_router_registered(self):
        """验证 /analytics 路由已注册。"""
        from app.api.v2.router import router
        paths = [r.path for r in router.routes]
        assert any("/analytics" in p for p in paths), f"/analytics 不在路由中: {paths}"
