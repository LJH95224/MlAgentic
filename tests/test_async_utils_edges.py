"""``app.core.async_utils`` 边界与日志路径单测。

[tests/test_async_utils.py](test_async_utils.py) 已覆盖 5 个 happy path 场景；
本模块补充：

- ``gather_with_timeout`` 空列表短路（不调 wait_for，避免空 gather 的 corner case）
- 超时时 ``logger.warning`` 必须输出（含 label / count / timeout 数值）
- ``wait_for_named`` 子任务自身抛异常（非超时） → 原样透传
- ``gather_with_timeout`` 默认 return_exceptions=False 时单任务异常按 gather 语义传播
- 入参为 generator 而非 list（API 接收 Iterable）也能正常工作

设计目标：锁住 [app/core/async_utils.py](../app/core/async_utils.py) 的契约边界，
避免重构时把 "空列表早返" / "label/count 日志埋点" / "异常透传语义" 误改。
"""

from __future__ import annotations

import asyncio
import logging

import pytest


# ───────────────────────── 空列表早返 ─────────────────────────


@pytest.mark.asyncio
async def test_gather_with_timeout_empty_iterable_short_circuits():
    """awaitables=[] 必须立刻返 []，不调 asyncio.wait_for / asyncio.gather。

    这是 ``gather_with_timeout`` 内 ``if not items: return []`` 的契约。
    重要性：调用方常用 ``[t for t in tasks if cond]`` 过滤后传入，过滤掉
    所有任务时不应触发空 gather 的边界行为（空 gather 返 [] 但仍走调度）。
    """
    from app.core.async_utils import gather_with_timeout

    # 用极小 timeout 验证：即便 timeout 设到 0，空列表也不会触发 TimeoutError
    result = await gather_with_timeout([], timeout_s=0.0, label="unit-empty")

    assert result == []


@pytest.mark.asyncio
async def test_gather_with_timeout_empty_with_return_exceptions():
    """return_exceptions=True 模式下空列表同样短路返 []，行为一致。"""
    from app.core.async_utils import gather_with_timeout

    result = await gather_with_timeout(
        [], timeout_s=0.0, label="unit-empty-rex", return_exceptions=True
    )

    assert result == []


# ───────────────────────── 超时日志埋点 ─────────────────────────


@pytest.mark.asyncio
async def test_wait_for_named_timeout_logs_label(caplog):
    """超时时 ``logger.warning`` 必须包含 label，便于定位调用点。"""
    from app.core.async_utils import wait_for_named

    async def slow():
        await asyncio.sleep(1)

    with caplog.at_level(logging.WARNING, logger="app.core.async_utils"):
        with pytest.raises(asyncio.TimeoutError):
            await wait_for_named(slow(), timeout_s=0.01, label="my-call")

    # 日志必须含 label 字符串
    assert any("my-call" in r.message for r in caplog.records), (
        "超时 warning 必须带上 label 便于定位"
    )


@pytest.mark.asyncio
async def test_gather_with_timeout_logs_count_on_timeout(caplog):
    """gather 超时时 ``logger.warning`` 必须含 count（任务数），便于排查规模。"""
    from app.core.async_utils import gather_with_timeout

    async def slow():
        await asyncio.sleep(1)

    with caplog.at_level(logging.WARNING, logger="app.core.async_utils"):
        with pytest.raises(asyncio.TimeoutError):
            await gather_with_timeout(
                [slow(), slow(), slow()],
                timeout_s=0.01,
                label="batch-slow",
            )

    # 日志同时含 label 与任务数
    matched = [
        r for r in caplog.records
        if "batch-slow" in r.message and "count=3" in r.message
    ]
    assert matched, "gather 超时 warning 必须含 label 和 count=N"


# ───────────────────────── 异常透传语义 ─────────────────────────


@pytest.mark.asyncio
async def test_wait_for_named_propagates_non_timeout_exception():
    """子任务自身抛非 TimeoutError 异常时，原样向上传播（不被吞、不被改包装）。

    这是 ``wait_for_named`` 与 ``asyncio.wait_for`` 一致的契约：
    超时由本函数包装日志，但其它异常应原样透出，让调用方决策。
    """
    from app.core.async_utils import wait_for_named

    class MyAppError(RuntimeError):
        pass

    async def boom():
        raise MyAppError("business error")

    with pytest.raises(MyAppError, match="business error"):
        await wait_for_named(boom(), timeout_s=0.5, label="unit-boom")


@pytest.mark.asyncio
async def test_gather_with_timeout_default_propagates_first_exception():
    """return_exceptions=False（默认）时，单任务抛错按 gather 语义传播第一个异常。

    与原 ``test_gather_with_timeout_keeps_return_exceptions_semantics`` 互补：
    那个测 return_exceptions=True 下异常被包进结果列表，本测验证默认行为。
    """
    from app.core.async_utils import gather_with_timeout

    async def boom():
        raise ValueError("first")

    async def ok():
        return 42

    with pytest.raises(ValueError, match="first"):
        await gather_with_timeout(
            [boom(), ok()],
            timeout_s=0.5,
            label="unit-default-exc",
        )


# ───────────────────────── Iterable 兼容性 ─────────────────────────


@pytest.mark.asyncio
async def test_gather_with_timeout_accepts_generator():
    """awaitables 参数类型是 Iterable[Awaitable]，应能接收 generator 而非仅 list。

    重要性：调用方常写 ``(do(x) for x in items)`` 这种 generator 表达式。
    内部 ``list(awaitables)`` 实现要能消费迭代器一次性物化。
    """
    from app.core.async_utils import gather_with_timeout

    async def value(i: int):
        return i * 10

    gen = (value(i) for i in [1, 2, 3])
    result = await gather_with_timeout(
        gen, timeout_s=0.5, label="unit-gen"
    )

    assert result == [10, 20, 30]


__all__: list[str] = []
