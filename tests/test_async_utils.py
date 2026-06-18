"""异步超时公共工具单测。"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_wait_for_named_returns_result():
    from app.core.async_utils import wait_for_named

    async def ok():
        return "done"

    assert await wait_for_named(ok(), timeout_s=0.2, label="unit-ok") == "done"


@pytest.mark.asyncio
async def test_wait_for_named_raises_timeout():
    from app.core.async_utils import wait_for_named

    async def slow():
        await asyncio.sleep(1)

    with pytest.raises(asyncio.TimeoutError):
        await wait_for_named(slow(), timeout_s=0.01, label="unit-slow")


@pytest.mark.asyncio
async def test_gather_with_timeout_returns_results():
    from app.core.async_utils import gather_with_timeout

    async def value(i: int):
        return i * 2

    result = await gather_with_timeout(
        [value(1), value(2), value(3)],
        timeout_s=0.2,
        label="unit-gather",
    )

    assert result == [2, 4, 6]


@pytest.mark.asyncio
async def test_gather_with_timeout_keeps_return_exceptions_semantics():
    from app.core.async_utils import gather_with_timeout

    async def boom():
        raise RuntimeError("x")

    result = await gather_with_timeout(
        [boom()],
        timeout_s=0.2,
        label="unit-gather-exc",
        return_exceptions=True,
    )

    assert len(result) == 1
    assert isinstance(result[0], RuntimeError)


@pytest.mark.asyncio
async def test_gather_with_timeout_raises_timeout():
    from app.core.async_utils import gather_with_timeout

    async def slow():
        await asyncio.sleep(1)

    with pytest.raises(asyncio.TimeoutError):
        await gather_with_timeout(
            [slow()],
            timeout_s=0.01,
            label="unit-gather-slow",
        )
