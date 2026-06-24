"""V2.0 Trace 采集器（OBS-01）。

核心类：
- Tracer：上下文管理器，贯穿一次 /v2/query 调用全链路
- tracer.step(step_type)：装饰器/上下文管理器，自动计时 + 写入 PG

设计要点：
- trace_id 在 Tracer 入口生成，贯穿全链路所有 step
- 每个 step 记录 step_type / step_latency_ms / step_input / step_output
- 根 step（parent_step=None）额外记录 total_latency_ms
- trace_enable=False 时所有操作短路，零开销
- 写入 PG 使用 fire-and-forget（B M-04，2026-06-22 起），不阻塞 /v2/query 主链路

用法：
    async with Tracer(session_id=session_id, kb_id=kb_id) as t:
        with t.step("parse", step_input={"filename": "test.pdf"}):
            blocks = parse_document_structured(path)
        with t.step("retrieve", step_input={"query": "台风", "top_k": 5}):
            results = hybrid_search(query)
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generator

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _trace_flush_done(task: asyncio.Task) -> None:
    """fire-and-forget 写入任务的 done callback（B M-04）。

    职责：捕获异常仅 warning，防止 PG 抖动反传到业务主链路；
    同时调一下 task.exception() 防 asyncio 报"never awaited"警告。
    """
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.warning("Trace 写入 PG 失败（已忽略）: %s", exc)


@dataclass
class TraceStep:
    """单个 trace 步骤记录。"""

    step_type: str
    parent_step: str | None = None
    step_latency_ms: int | None = None
    step_input: dict | None = None
    step_output: dict | None = None
    model_name: str | None = None
    token_count: int | None = None
    error_message: str | None = None
    # 内部用：开始时间戳
    _start_time: float = field(default=0.0, repr=False)


class Tracer:
    """V2.0 推理链路追踪器（OBS-01）。

    作为上下文管理器使用，进入时生成 trace_id，退出时计算总耗时
    并将所有步骤批量写入 PG agent_traces 表。

    示例：
        async with Tracer(session_id=sid, kb_id=kb_id) as t:
            with t.step("parse", step_input={"file": "a.pdf"}):
                ...
            with t.step("retrieve", step_input={"query": "台风"}):
                ...
    """

    def __init__(
        self,
        *,
        session_id: uuid.UUID | None = None,
        kb_id: uuid.UUID | None = None,
        trace_id: str | None = None,
    ):
        self.trace_id = trace_id or uuid.uuid4().hex[:16]
        self.session_id = session_id
        self.kb_id = kb_id
        self.steps: list[TraceStep] = []
        self._start_time: float = 0.0
        self._total_latency_ms: int | None = None
        self._enabled: bool = True
        self._parent_stack: list[str] = []  # 当前嵌套的 parent step_type

    async def __aenter__(self) -> "Tracer":
        settings = get_settings()
        self._enabled = settings.trace_enable

        if not self._enabled:
            return self

        self._start_time = time.perf_counter()
        logger.info("Trace 开始: trace_id=%s session=%s kb=%s", self.trace_id, self.session_id, self.kb_id)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if not self._enabled:
            return

        elapsed = time.perf_counter() - self._start_time
        self._total_latency_ms = int(elapsed * 1000)

        # B M-04：fire-and-forget 异步写入 PG，不阻塞主链路。
        # 高 QPS 下 trace 写入是 /v2/query 的尾部瓶颈；create_task 后立即返回，
        # 让 ASGI worker 能去处理下一个请求。task 异常仅 warning，不冒泡到业务。
        try:
            task = asyncio.create_task(self._flush_to_db())
            task.add_done_callback(_trace_flush_done)
        except RuntimeError as e:
            # 极端边角：当前线程没有 running event loop（如同步 fixture
            # 用 trace 但忘了 await）。降级为同步 await 走老路径。
            logger.warning("Trace 异步调度失败，降级同步写: %s", e)
            try:
                await self._flush_to_db()
            except Exception as inner:  # noqa: BLE001
                logger.warning("Trace 同步写入也失败（已忽略）: %s", inner)

        logger.info(
            "Trace 结束: trace_id=%s steps=%d total=%dms",
            self.trace_id,
            len(self.steps),
            self._total_latency_ms,
        )

    @contextmanager
    def step(
        self,
        step_type: str,
        *,
        step_input: dict | None = None,
        model_name: str | None = None,
    ) -> Generator[TraceStep, None, None]:
        """步骤上下文管理器：自动计时 + 记录输入。

        用法：
            with tracer.step("parse", step_input={"file": "a.pdf"}) as s:
                result = do_parse()
                s.step_output = {"blocks": len(result)}

        Args:
            step_type: 步骤类型（parse / retrieve / rerank / generate / citation_parse 等）
            step_input: 步骤输入快照（可选）
            model_name: 步骤使用的 LLM 模型名（可选）
        """
        if not self._enabled:
            # 禁用时返回空 step 对象，不记录
            yield TraceStep(step_type=step_type)
            return

        parent_step = self._parent_stack[-1] if self._parent_stack else None
        record = TraceStep(
            step_type=step_type,
            parent_step=parent_step,
            step_input=step_input,
            model_name=model_name,
        )
        record._start_time = time.perf_counter()

        self._parent_stack.append(step_type)
        try:
            yield record
        except Exception as e:  # noqa: BLE001
            # 步骤失败时记录错误
            record.error_message = f"{type(e).__name__}: {e}"[:2000]
            raise
        finally:
            self._parent_stack.pop()
            elapsed = time.perf_counter() - record._start_time
            record.step_latency_ms = int(elapsed * 1000)
            self.steps.append(record)

    async def _flush_to_db(self) -> None:
        """批量写入 agent_traces 表（异步）。

        B M-04 起由 __aexit__ 走 asyncio.create_task fire-and-forget 调用，
        异常通过 _trace_flush_done 回调捕获，不冒泡到业务主链路。
        通过 AsyncSessionLocal 走与业务层一致的 asyncpg 引擎。
        """
        from sqlalchemy import insert
        from app.db.session import AsyncSessionLocal
        from app.models.agent_trace import AgentTrace

        if not self.steps:
            return

        rows = []
        for s in self.steps:
            rows.append({
                "trace_id": self.trace_id,
                "session_id": self.session_id,
                "kb_id": self.kb_id,
                "step_type": s.step_type,
                "parent_step": s.parent_step,
                "step_latency_ms": s.step_latency_ms,
                "total_latency_ms": self._total_latency_ms if s.parent_step is None else None,
                "step_input": s.step_input,
                "step_output": s.step_output,
                "model_name": s.model_name,
                "token_count": s.token_count,
                "error_message": s.error_message,
            })

        async with AsyncSessionLocal() as session:
            await session.execute(insert(AgentTrace), rows)
            await session.commit()

        logger.debug("Trace 写入 PG: trace_id=%s rows=%d", self.trace_id, len(rows))


def make_trace_id() -> str:
    """生成短 trace_id（16 字符 hex）。"""
    return uuid.uuid4().hex[:16]


__all__ = ["Tracer", "TraceStep", "make_trace_id"]
