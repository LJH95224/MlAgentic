"""异步超时公共工具。

本模块只提供技术性超时包装，不决定业务降级语义。
调用方负责决定超时后是软失败、任务失败还是继续抛出。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Iterable
from typing import TypeVar, overload

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def wait_for_named(
    awaitable: Awaitable[T],
    *,
    timeout_s: float,
    label: str,
) -> T:
    """给单个 awaitable 加命名硬超时。"""
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_s)
    except asyncio.TimeoutError:
        logger.warning("异步调用超时 label=%s timeout=%.1fs", label, timeout_s)
        raise


@overload
async def gather_with_timeout(
    awaitables: Iterable[Awaitable[T]],
    *,
    timeout_s: float,
    label: str,
    return_exceptions: bool = False,
) -> list[T]:
    ...


async def gather_with_timeout(
    awaitables: Iterable[Awaitable[T]],
    *,
    timeout_s: float,
    label: str,
    return_exceptions: bool = False,
) -> list[T | BaseException]:
    """给 asyncio.gather 加整体硬超时。

    Args:
        awaitables: 待并发执行的 awaitable 列表。
        timeout_s: 整组调用的最大等待秒数。
        label: 日志标签，便于定位调用点。
        return_exceptions: 透传给 ``asyncio.gather``。

    Returns:
        与 ``asyncio.gather`` 一致的结果列表。

    Raises:
        asyncio.TimeoutError: 整组调用超过 timeout_s。
        Exception: 当 return_exceptions=False 时，子任务异常按 gather 语义传播。
    """
    items = list(awaitables)
    if not items:
        return []

    try:
        return await asyncio.wait_for(
            asyncio.gather(*items, return_exceptions=return_exceptions),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "批量异步调用超时 label=%s count=%d timeout=%.1fs",
            label,
            len(items),
            timeout_s,
        )
        raise
