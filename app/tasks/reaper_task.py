"""卡死 processing 文件回收周期任务。

【问题】
`kb_files.status` 状态机：``pending → processing → completed / failed``。但有几种路径
会让文件永远卡在 ``processing``、PG 没人改：

1. **Celery hard timeout 触发**——``celery_app.py`` 配的 ``task_time_limit=30min``
   会发 ``SIGKILL`` 杀子进程，不走 try/except，``_mark_failed_safe`` 永远不会执行。
2. **Worker 进程被 SIGKILL / OOM**——同上。
3. **broker 重投失败 / 消息丢失**——``task_acks_late=True`` 保至少一次，但 broker
   自身故障 + result backend 过期后，谁也不知道这条任务出了什么事。
4. **Worker 重启时正在跑的任务**——一般会重投，但 broker 状态可能不同步。

【方案】
- ``kb_files`` 新增 ``updated_at`` 字段：每次 ``_set_progress`` 更新进度时由
  SQLAlchemy ``onupdate=func.now()`` 自动刷新，等于"任务心跳"。
- 本任务周期跑（默认 10min），扫 ``status=processing AND updated_at < now() - threshold``
  的行，调 ``_mark_failed_safe`` 走标准失败补偿（清 Milvus / Neo4j 残留 + 标 failed）。
- 阈值默认 35min（Celery hard timeout 30min + 5min 缓冲），可由
  ``INGEST_STALE_TIMEOUT_S`` 覆盖。

【为什么用 created_at 不够？】
大文件 NER / Embedding 合法跑 30min+ 也常见。但每个 ``_step_xxx`` 完成后都会
``_set_progress`` 刷 ``updated_at``，所以单步内部慢 25min 不会被误判——只有真的"心跳停了"
才会触发回收。

【为什么用 Celery beat 而不是 OS cron？】
- 进程同源：和 ingest worker 在同一 Celery 应用里，部署一处即可。
- Windows 友好：开发态 `celery -A app.tasks.celery_app beat -l info` 一行启动。
- 与 ingest_task 共用 ``task_resources`` / ``_mark_failed_safe`` 失败补偿，逻辑收敛。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.models.kb_file import FILE_STATUS_PROCESSING, KbFile
from app.tasks._resources import task_resources
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _scan_stale_processing_files(threshold_s: int) -> list[tuple[uuid.UUID, uuid.UUID, datetime]]:
    """扫描卡死 processing 文件，返回 (file_id, kb_id, updated_at) 列表。

    SELECT 与 ``_mark_failed_safe`` 之间不持锁——回收任务幂等：即使被并发跑两次，
    第二次扫到时 ``status`` 已被改成 ``failed`` 就再也匹配不到。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=threshold_s)
    async with task_resources() as resources:
        async with resources.db() as session:
            rows = (
                await session.execute(
                    select(KbFile.id, KbFile.kb_id, KbFile.updated_at)
                    .where(KbFile.status == FILE_STATUS_PROCESSING)
                    .where(KbFile.updated_at < cutoff)
                )
            ).all()
    return [(r.id, r.kb_id, r.updated_at) for r in rows]


async def _reap_one(file_id: uuid.UUID, kb_id: uuid.UUID, last_heartbeat: datetime) -> bool:
    """回收单个卡死文件：调标准失败补偿。

    Returns:
        True 表示已标 failed；False 表示中途失败（不抛出，让外层继续处理下一条）。
    """
    # 复用 ingest_task 里的失败补偿，避免逻辑分叉。延迟 import 避免循环依赖。
    from app.tasks.ingest_task import _mark_failed_safe

    error_msg = (
        f"任务卡死回收：status=processing 但 updated_at={last_heartbeat.isoformat()} "
        f"已超过 ingest_stale_timeout_s 阈值；可能由 Celery hard timeout / worker SIGKILL / "
        f"OOM 等不走 try-except 的路径导致。"
    )
    try:
        await _mark_failed_safe(
            str(file_id),
            kb_id=str(kb_id),
            error_message=error_msg,
        )
        logger.info("reaper 已回收卡死文件 file_id=%s kb_id=%s", file_id, kb_id)
        return True
    except Exception as e:  # noqa: BLE001
        logger.error(
            "reaper 回收失败 file_id=%s kb_id=%s err=%s（下一轮会重试）",
            file_id, kb_id, e,
        )
        return False


async def _main() -> dict:
    """扫描 + 回收主流程。"""
    settings = get_settings()
    if not settings.ingest_reaper_enable:
        logger.debug("INGEST_REAPER_ENABLE=False 跳过本次扫描")
        return {"scanned": 0, "reaped": 0, "skipped": True}

    threshold_s = settings.ingest_stale_timeout_s
    stale_rows = await _scan_stale_processing_files(threshold_s)
    if not stale_rows:
        logger.debug("reaper 扫描完成：无卡死文件 threshold=%ds", threshold_s)
        return {"scanned": 0, "reaped": 0, "skipped": False}

    logger.warning(
        "reaper 发现 %d 个卡死 processing 文件 threshold=%ds",
        len(stale_rows), threshold_s,
    )

    reaped = 0
    for file_id, kb_id, last_heartbeat in stale_rows:
        if await _reap_one(file_id, kb_id, last_heartbeat):
            reaped += 1

    return {
        "scanned": len(stale_rows),
        "reaped": reaped,
        "threshold_s": threshold_s,
        "skipped": False,
    }


@celery_app.task(
    name="app.tasks.reaper_task.reap_stale_processing_files",
    # 这个任务**不**重试：扫表幂等，下一周期自然会再扫
    max_retries=0,
    # 软超时 1 min / 硬超时 90s——回收任务本身不应该挂太久；
    # 单条 _mark_failed_safe 内部对 Milvus/Neo4j 都有 try/except 软降级
    soft_time_limit=60,
    time_limit=90,
)
def reap_stale_processing_files() -> dict:
    """Celery beat 周期入口：扫描并回收卡死 processing 文件。"""
    logger.info("reaper 任务开始")
    try:
        return asyncio.run(_main())
    except Exception as exc:  # noqa: BLE001
        # 整个回收任务挂掉不应该影响 worker，吞掉错误即可；下一周期重试
        logger.error("reaper 任务异常：%s", exc, exc_info=True)
        return {"scanned": 0, "reaped": 0, "error": f"{type(exc).__name__}: {exc}"}


__all__ = ["reap_stale_processing_files"]
