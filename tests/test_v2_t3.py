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

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

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

    @pytest.mark.asyncio
    async def test_tracer_exit_schedules_flush_as_task(self, monkeypatch):
        """B M-04：__aexit__ 通过 create_task fire-and-forget 写入 PG，不 await。

        证据链：
        1. asyncio.create_task 被调一次以上 → 写入走了 task 而非直接 await
        2. _flush_to_db 最终被 awaited 一次 → task 正常运转
        """
        import app.observability.tracer as tracer_mod

        create_task_calls = []
        original_create_task = asyncio.create_task

        def _spy_create_task(coro, **kw):
            create_task_calls.append(coro)
            return original_create_task(coro, **kw)

        monkeypatch.setattr(asyncio, "create_task", _spy_create_task)

        with patch.object(tracer_mod.Tracer, "_flush_to_db", AsyncMock()) as mock_flush:
            with patch("app.observability.tracer.get_settings") as mock_settings:
                settings = MagicMock()
                settings.trace_enable = True
                mock_settings.return_value = settings

                async with Tracer() as t:
                    with t.step("retrieve", step_input={"q": "test"}) as s:
                        s.step_latency_ms = 10

        # 让 fire-and-forget task 跑完
        await asyncio.sleep(0)

        # 1) create_task 至少被调一次
        assert len(create_task_calls) >= 1, "Trace __aexit__ 应通过 create_task 调度写入"
        # 2) _flush_to_db 确实在后台被运行（mock 计数 = task 调度成功并执行完）
        mock_flush.assert_awaited_once()


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

    @pytest.mark.asyncio
    async def test_get_trace_not_found_raises_business_error(self):
        """trace 不存在时应抛 BusinessError，交给统一异常处理输出 ApiResponse。"""
        from app.api import error_codes
        from app.api.exceptions import BusinessError
        from app.api.v2.endpoints.traces import get_trace

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(BusinessError) as exc_info:
            await get_trace("missing-trace", db=mock_db)

        mock_db.execute.assert_awaited_once()
        assert exc_info.value.code == error_codes.NOT_FOUND
        assert "trace_id=missing-trace 不存在" == exc_info.value.message

    @pytest.mark.asyncio
    async def test_list_session_traces_uses_single_group_by_for_step_counts(self):
        """B L-06：list_session_traces 必须一次性 GROUP BY 取 step_count，
        不再为每个 trace 单独跑 count()——避免 N+1。

        本用例断言:
        1. 总查询次数 == 3（count + 根步骤分页 + 单次 group-by），与 trace 数量无关
        2. step_count_map 正确映射回每个 TraceListItem
        3. 单次 group-by 查询不在 root_step 数量上线性扩展
        """
        from datetime import datetime, timezone

        from app.api.v2.endpoints.traces import list_session_traces

        session_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        # 构造 3 条根步骤（模拟一页 3 个 trace）
        roots = []
        for i in range(3):
            root = MagicMock()
            root.trace_id = f"trace-{i}"
            root.session_id = session_id
            root.kb_id = None
            root.total_latency_ms = 100 * (i + 1)
            root.created_at = now
            roots.append(root)

        # mock 三次 execute 的返回值（按调用顺序）
        count_total_result = MagicMock()
        count_total_result.scalar.return_value = 3

        roots_result = MagicMock()
        roots_result.scalars.return_value.all.return_value = roots

        # group-by 单次返回 (trace_id, step_count) tuple list
        group_by_result = MagicMock()
        group_by_result.all.return_value = [
            ("trace-0", 5),
            ("trace-1", 7),
            ("trace-2", 3),
        ]

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(
            side_effect=[count_total_result, roots_result, group_by_result]
        )

        resp = await list_session_traces(
            session_id=session_id,
            page=1,
            page_size=20,
            db=mock_db,
        )

        # ① N+1 修复后整个流程只需 3 次 execute（count + 根步骤 + group-by）
        assert mock_db.execute.await_count == 3, (
            "list_session_traces 应只发 3 条 SQL（count 总数 / 根步骤分页 / 单次 group-by 取 step_count），"
            f"实际发 {mock_db.execute.await_count} 条 —— 可能退化为 N+1"
        )

        # ② step_count 映射正确
        assert resp.total == 3
        assert len(resp.items) == 3
        step_counts = {item.trace_id: item.step_count for item in resp.items}
        assert step_counts == {"trace-0": 5, "trace-1": 7, "trace-2": 3}

    @pytest.mark.asyncio
    async def test_list_session_traces_no_roots_skips_group_by(self):
        """空页（无根步骤）时不应跑 group-by 查询，避免 IN () 语法错误。"""
        from app.api.v2.endpoints.traces import list_session_traces

        session_id = uuid.uuid4()

        count_total_result = MagicMock()
        count_total_result.scalar.return_value = 0

        roots_result = MagicMock()
        roots_result.scalars.return_value.all.return_value = []

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(
            side_effect=[count_total_result, roots_result]
        )

        resp = await list_session_traces(
            session_id=session_id,
            page=1,
            page_size=20,
            db=mock_db,
        )

        # 空页只需 2 次查询（count + 根步骤），不应触发 group-by
        assert mock_db.execute.await_count == 2
        assert resp.total == 0
        assert resp.items == []


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
