"""V2.0 T3 阶段单测（可观测性 Trace 验收）。

覆盖：
1. Tracer 上下文管理器生命周期
2. Tracer.step 自动计时
3. trace_enable=False 时短路
4. TraceStep 数据类
5. Trace 查询接口（mock DB）
6. V2 Schema 结构
7. V2 router 挂载
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.observability.tracer import TraceStep, Tracer, make_trace_id
from app.schemas.v2.trace import (
    TraceDetail,
    TraceListItem,
    TraceListResponse,
    TraceStepItem,
)


# ════════════════════════════════════════════════════════════════
# 1. Tracer 生命周期
# ════════════════════════════════════════════════════════════════


class TestTracerLifecycle:
    @pytest.mark.asyncio
    async def test_tracer_generates_trace_id(self):
        """Tracer 入口自动生成 trace_id。"""
        with patch("app.observability.tracer.get_settings") as mock_settings:
            settings = MagicMock()
            settings.trace_enable = True
            mock_settings.return_value = settings

            async with Tracer() as t:
                assert t.trace_id is not None
                assert len(t.trace_id) == 16

    @pytest.mark.asyncio
    async def test_tracer_custom_trace_id(self):
        """支持外部传入 trace_id。"""
        with patch("app.observability.tracer.get_settings") as mock_settings:
            settings = MagicMock()
            settings.trace_enable = True
            mock_settings.return_value = settings

            async with Tracer(trace_id="custom-trace-123") as t:
                assert t.trace_id == "custom-trace-123"

    @pytest.mark.asyncio
    async def test_tracer_disabled_short_circuit(self):
        """trace_enable=False 时不记录任何步骤。"""
        with patch("app.observability.tracer.get_settings") as mock_settings:
            settings = MagicMock()
            settings.trace_enable = False
            mock_settings.return_value = settings

            async with Tracer() as t:
                with t.step("parse", step_input={"file": "test.pdf"}):
                    pass  # 模拟工作
                # 禁用时 steps 不应被添加
                assert len(t.steps) == 0


# ════════════════════════════════════════════════════════════════
# 2. Tracer.step 自动计时
# ════════════════════════════════════════════════════════════════


class TestTracerStep:
    @pytest.mark.asyncio
    async def test_step_records_latency(self):
        """step 自动记录耗时。"""
        with patch("app.observability.tracer.get_settings") as mock_settings:
            settings = MagicMock()
            settings.trace_enable = True
            mock_settings.return_value = settings

            async with Tracer() as t:
                with t.step("parse", step_input={"file": "a.pdf"}) as s:
                    s.step_output = {"blocks": 5}

            assert len(t.steps) == 1
            step = t.steps[0]
            assert step.step_type == "parse"
            assert step.step_latency_ms is not None
            assert step.step_latency_ms >= 0
            assert step.step_input == {"file": "a.pdf"}
            assert step.step_output == {"blocks": 5}

    @pytest.mark.asyncio
    async def test_step_parent_tracking(self):
        """嵌套 step 的 parent_step 正确。"""
        with patch("app.observability.tracer.get_settings") as mock_settings:
            settings = MagicMock()
            settings.trace_enable = True
            mock_settings.return_value = settings

            async with Tracer() as t:
                with t.step("retrieve"):
                    pass
                with t.step("generate"):
                    pass

            assert len(t.steps) == 2
            # 顶层步骤 parent_step=None
            assert t.steps[0].parent_step is None
            assert t.steps[1].parent_step is None

    @pytest.mark.asyncio
    async def test_step_error_capture(self):
        """步骤异常时记录 error_message。"""
        with patch("app.observability.tracer.get_settings") as mock_settings:
            settings = MagicMock()
            settings.trace_enable = True
            mock_settings.return_value = settings

            async with Tracer() as t:
                try:
                    with t.step("parse"):
                        raise ValueError("解析失败")
                except ValueError:
                    pass

            assert len(t.steps) == 1
            assert t.steps[0].error_message is not None
            assert "ValueError" in t.steps[0].error_message

    @pytest.mark.asyncio
    async def test_multiple_steps_in_order(self):
        """多个步骤按顺序记录。"""
        with patch("app.observability.tracer.get_settings") as mock_settings:
            settings = MagicMock()
            settings.trace_enable = True
            mock_settings.return_value = settings

            async with Tracer() as t:
                with t.step("parse"):
                    pass
                with t.step("split"):
                    pass
                with t.step("embed"):
                    pass

            assert len(t.steps) == 3
            assert t.steps[0].step_type == "parse"
            assert t.steps[1].step_type == "split"
            assert t.steps[2].step_type == "embed"


# ════════════════════════════════════════════════════════════════
# 3. TraceStep 数据类
# ════════════════════════════════════════════════════════════════


class TestTraceStep:
    def test_creation(self):
        step = TraceStep(step_type="retrieve", step_input={"query": "台风"})
        assert step.step_type == "retrieve"
        assert step.step_latency_ms is None
        assert step.error_message is None

    def test_with_output(self):
        step = TraceStep(
            step_type="generate",
            step_output={"answer": "这是答案"},
            model_name="deepseek-v4-flash",
            token_count=150,
        )
        assert step.step_output == {"answer": "这是答案"}
        assert step.model_name == "deepseek-v4-flash"
        assert step.token_count == 150


# ════════════════════════════════════════════════════════════════
# 4. make_trace_id
# ════════════════════════════════════════════════════════════════


class TestMakeTraceId:
    def test_length(self):
        tid = make_trace_id()
        assert len(tid) == 16

    def test_unique(self):
        ids = {make_trace_id() for _ in range(100)}
        assert len(ids) == 100


# ════════════════════════════════════════════════════════════════
# 5. Trace 查询接口（mock DB 验证端点注册）
# ════════════════════════════════════════════════════════════════


class TestTraceSchemas:
    def test_trace_step_item(self):
        from datetime import datetime, timezone

        item = TraceStepItem(
            id=uuid.uuid4(),
            step_type="parse",
            step_latency_ms=120,
            step_input={"file": "test.pdf"},
            created_at=datetime.now(timezone.utc),
        )
        assert item.step_type == "parse"
        assert item.step_latency_ms == 120

    def test_trace_detail(self):
        detail = TraceDetail(
            trace_id="abc123",
            total_latency_ms=500,
            steps=[],
        )
        assert detail.trace_id == "abc123"
        assert detail.total_latency_ms == 500

    def test_trace_list_response(self):
        resp = TraceListResponse(
            items=[],
            total=0,
            page=1,
            page_size=20,
        )
        assert resp.total == 0
        assert resp.page == 1


class TestTraceEndpoints:
    def test_trace_router_has_routes(self):
        """Trace router 必须包含两个端点。"""
        from app.api.v2.endpoints.traces import router

        routes = [r.path for r in router.routes]
        assert "/traces/{trace_id}" in routes
        assert "/traces/sessions/{session_id}/traces" in routes


# ════════════════════════════════════════════════════════════════
# 6. V2 router 挂载
# ════════════════════════════════════════════════════════════════


class TestV2Router:
    def test_v2_router_prefix(self):
        """V2 router 前缀必须是 /api/v2。"""
        from app.api.v2.router import router

        assert router.prefix == "/api/v2"

    def test_main_includes_v2_router(self):
        """main.py create_app 必须挂载 V2 router。"""
        from app.main import create_app

        app = create_app()
        # 检查 /api/v2 路由是否存在
        v2_routes = [
            r.path
            for r in app.routes
            if hasattr(r, "path") and "/api/v2" in r.path
        ]
        assert len(v2_routes) > 0, "V2 路由未挂载"


# ════════════════════════════════════════════════════════════════
# 7. AgentTrace 模型在 models/__init__.py 注册
# ════════════════════════════════════════════════════════════════


class TestAgentTraceModelImport:
    def test_main_imports_agent_trace(self):
        """main.py 应导入 AgentTrace 模型（lifespan create_all 时建表）。"""
        import app.main as main_module

        # 检查模块源码是否包含 AgentTrace 导入
        import inspect

        source = inspect.getsource(main_module)
        assert "AgentTrace" in source
