"""外存清理失败文件 / KB 的补偿周期任务（A P1-9）。

【问题背景】
KB 删除（kb_service.delete_kb）与 KbFile 删除（kb_file_service.delete_file）的主路径
会同步清理 Milvus / Neo4j，但外存抖动（Milvus 临时断开 / Neo4j 短时不可达等）会导致：

- 清理失败 → status 改 pending_cleanup，让 reaper 兜底重试
- 但没人"再试一次"，pending_cleanup 就永远卡在那里

【方案】
本任务与 reaper_task（P1-11）共用 Celery beat 框架，独立调度：

1. 扫 `KnowledgeBase.status = pending_cleanup AND retry_count < max`，重试 Milvus drop + Neo4j 删
2. 扫 `KbFile.status = pending_cleanup AND retry_count < max`，重试 Milvus delete + Neo4j 删
3. 成功 → PG 真删（KB 级删整行；文件级删行 + KB 计数回滚 + 磁盘清）
4. 失败 → cleanup_retry_count += 1（下一轮再来）
5. retry_count >= max → 仅告警不再重试，等运维介入

【为什么与 reaper_task 分开？】
- reaper_task 的扫描条件是 `status=processing AND updated_at < threshold`，完全不同
- reaper_task 的补偿动作是 `_mark_failed_safe`（清残留 + 标 failed），这里是标 "pending_cleanup" 的再重试
- 合并会引入"一把扫两张表"的复杂度和嵌套条件，不如各管各

P1-9 · 2026-06-22
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import select, update

from app.core.config import get_settings
from app.models.kb_file import FILE_STATUS_PENDING_CLEANUP, KbFile
from app.models.knowledge_base import KB_STATUS_PENDING_CLEANUP, KnowledgeBase
from app.tasks._resources import task_resources
from app.tasks.celery_app import celery_app
from app.rag.milvus_client import drop_kb_collection

logger = logging.getLogger(__name__)


# ──────────────────── KB 扫描 + 补偿 ────────────────────


async def _scan_pending_cleanup_kbs(
    retry_cap: int,
) -> list[tuple[uuid.UUID, int]]:
    """扫描 KB 表中 status=pending_cleanup 且重试次数未达上限的行。

    Returns:
        [(kb_id, cleanup_retry_count), ...]，按 updated_at 升序（旧的优先）
    """
    async with task_resources() as resources:
        async with resources.db() as session:
            rows = (
                await session.execute(
                    select(KnowledgeBase.id, KnowledgeBase.cleanup_retry_count)
                    .where(KnowledgeBase.status == KB_STATUS_PENDING_CLEANUP)
                    .where(KnowledgeBase.cleanup_retry_count < retry_cap)
                    .order_by(KnowledgeBase.updated_at.asc())
                )
            ).all()
    return [(r.id, r.cleanup_retry_count) for r in rows]


async def _reap_one_kb(kb_id: uuid.UUID, retry_count: int) -> bool:
    """重试单个 KB 的外存清理。

    1. Milvus drop_collection（幂等；collection 不存在 = 已清干净）
    2. Neo4j DETACH DELETE 所有 (n {kb_id})（同款幂等）
    3. 都成功 → PG 真删；任一失败 → retry_count + 1

    Returns:
        True — 本轮成功（KB 已真删）；False — 失败，下一轮继续
    """
    logger.info(
        "cleanup_reaper: 重试 KB 外存清理 kb_id=%s retry=%d",
        kb_id,
        retry_count,
    )

    # 1) Milvus drop（幂等）
    milvus_ok = True
    try:
        drop_kb_collection(kb_id)
    except RuntimeError as e:
        logger.warning(
            "cleanup_reaper: KB Milvus 清理重试失败 kb_id=%s retry=%d err=%s",
            kb_id,
            retry_count,
            e,
        )
        milvus_ok = False

    # 2) Neo4j 删（幂等）
    neo4j_ok = await _try_cleanup_kb_neo4j(kb_id)

    async with task_resources() as resources:
        if milvus_ok and neo4j_ok:
            async with resources.db() as session:
                kb = await session.get(KnowledgeBase, kb_id)
                if kb is not None:
                    await session.delete(kb)
                    await session.commit()
            logger.info(
                "cleanup_reaper: KB 外存清理成功并真删 kb_id=%s",
                kb_id,
            )
            return True
        else:
            # 失败 → retry_count + 1
            async with resources.db() as session:
                await session.execute(
                    update(KnowledgeBase)
                    .where(KnowledgeBase.id == kb_id)
                    .values(cleanup_retry_count=KnowledgeBase.cleanup_retry_count + 1)
                )
                await session.commit()
            logger.warning(
                "cleanup_reaper: KB 外存清理仍失败 kb_id=%s retry=%d",
                kb_id,
                retry_count + 1,
            )
            return False


async def _try_cleanup_kb_neo4j(kb_id: uuid.UUID) -> bool:
    """清理 KB 在 Neo4j 的子图，幂等。"""
    try:
        from app.core.config import get_settings
        from app.kg.neo4j_client import get_neo4j_driver
    except ImportError:
        return True

    try:
        driver = get_neo4j_driver()
    except RuntimeError:
        return True

    settings = get_settings()
    cypher = "MATCH (n {kb_id: $kb_id}) DETACH DELETE n"
    try:
        async with driver.session(database=settings.neo4j_database) as sess:
            await sess.run(cypher, kb_id=str(kb_id))
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "cleanup_reaper: KB Neo4j 清理重试失败 kb_id=%s err=%s",
            kb_id,
            e,
        )
        return False


# ──────────────────── KbFile 扫描 + 补偿 ────────────────────


async def _scan_pending_cleanup_files(
    retry_cap: int,
) -> list[tuple[uuid.UUID, uuid.UUID, int]]:
    """扫描 kb_files 中 status=pending_cleanup 且重试次数未达上限的行。

    Returns:
        [(file_id, kb_id, cleanup_retry_count), ...]，按 updated_at 升序
    """
    async with task_resources() as resources:
        async with resources.db() as session:
            rows = (
                await session.execute(
                    select(KbFile.id, KbFile.kb_id, KbFile.cleanup_retry_count)
                    .where(KbFile.status == FILE_STATUS_PENDING_CLEANUP)
                    .where(KbFile.cleanup_retry_count < retry_cap)
                    .order_by(KbFile.updated_at.asc())
                )
            ).all()
    return [(r.id, r.kb_id, r.cleanup_retry_count) for r in rows]


async def _reap_one_file(
    file_id: uuid.UUID, kb_id: uuid.UUID, retry_count: int
) -> bool:
    """重试单个 KbFile 的外存清理。

    Returns:
        True — 本轮成功（PG 真删）；False — 失败，下一轮 +1 retry
    """
    logger.info(
        "cleanup_reaper: 重试文件外存清理 file_id=%s kb_id=%s retry=%d",
        file_id,
        kb_id,
        retry_count,
    )

    # 1) Milvus 删
    from app.services.kb_file_service import _cleanup_milvus_chunks_for_file

    milvus_ok = await _cleanup_milvus_chunks_for_file(kb_id, file_id)

    # 2) Neo4j 删
    from app.services.kb_file_service import _cleanup_neo4j_entities_for_file

    neo4j_ok = await _cleanup_neo4j_entities_for_file(kb_id, file_id)

    async with task_resources() as resources:
        if milvus_ok and neo4j_ok:
            # 全成功 → PG 真删 + KB 计数回滚 + 磁盘清
            async with resources.db() as session:
                f = await session.get(KbFile, file_id)
                if f is not None:
                    old_chunk_count = f.chunk_count
                    await session.delete(f)
                    await session.execute(
                        update(KnowledgeBase)
                        .where(KnowledgeBase.id == kb_id)
                        .values(
                            file_count=KnowledgeBase.file_count - 1,
                            chunk_count=KnowledgeBase.chunk_count - old_chunk_count,
                        )
                    )
                    await session.commit()

                    # 磁盘（尽力）
                    from app.services.kb_file_service import _safe_remove_disk

                    _safe_remove_disk(f.file_path)

            logger.info(
                "cleanup_reaper: 文件外存清理成功并真删 file_id=%s kb_id=%s",
                file_id,
                kb_id,
            )
            return True
        else:
            async with resources.db() as session:
                await session.execute(
                    update(KbFile)
                    .where(KbFile.id == file_id)
                    .values(
                        cleanup_retry_count=KbFile.cleanup_retry_count + 1,
                        error_message=(
                            "[P1-9-reaper] 外存清理重试失败 "
                            f"(Milvus={'OK' if milvus_ok else 'FAIL'}, "
                            f"Neo4j={'OK' if neo4j_ok else 'FAIL'})"
                        ),
                    )
                )
                await session.commit()
            logger.warning(
                "cleanup_reaper: 文件外存清理仍失败 file_id=%s kb_id=%s retry=%d",
                file_id,
                kb_id,
                retry_count + 1,
            )
            return False


# ──────────────────── 入口 ────────────────────


async def _main() -> dict:
    """扫描 + 补偿主流程。"""
    settings = get_settings()
    if not settings.cleanup_reaper_enable:
        logger.debug("CLEANUP_REAPER_ENABLE=False 跳过本次扫描")
        return {"kbs_scanned": 0, "files_scanned": 0, "skipped": True}

    retry_cap = settings.cleanup_reaper_max_retry

    # —— KB ——
    stale_kbs = await _scan_pending_cleanup_kbs(retry_cap)
    reaped_kbs = 0
    for kb_id, retry in stale_kbs:
        if await _reap_one_kb(kb_id, retry):
            reaped_kbs += 1

    # —— KbFile ——
    stale_files = await _scan_pending_cleanup_files(retry_cap)
    reaped_files = 0
    for file_id, kb_id, retry in stale_files:
        if await _reap_one_file(file_id, kb_id, retry):
            reaped_files += 1

    if stale_kbs or stale_files:
        logger.warning(
            "cleanup_reaper 扫描结果: KB=%d(%d已回收) 文件=%d(%d已回收) retry_cap=%d",
            len(stale_kbs),
            reaped_kbs,
            len(stale_files),
            reaped_files,
            retry_cap,
        )

    return {
        "kbs_scanned": len(stale_kbs),
        "kbs_reaped": reaped_kbs,
        "files_scanned": len(stale_files),
        "files_reaped": reaped_files,
        "retry_cap": retry_cap,
        "skipped": False,
    }


@celery_app.task(
    name="app.tasks.cleanup_reaper_task.reap_pending_cleanup",
    max_retries=0,
    soft_time_limit=120,
    time_limit=180,
)
def reap_pending_cleanup() -> dict:
    """Celery beat 周期入口：重试 pending_cleanup 外存清理。

    与 reaper_task.reap_stale_processing_files 职责正交：
    - reaper_task：扫 status=processing 且心跳超时的文件 → 标 failed
    - 本任务：扫 status=pending_cleanup 的 KB/文件 → 重试外存清理 → 成功则真删
    """
    logger.info("cleanup_reaper 任务开始")
    try:
        return asyncio.run(_main())
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "cleanup_reaper 任务异常：%s", exc, exc_info=True
        )
        return {
            "kbs_scanned": 0,
            "files_scanned": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }


__all__ = ["reap_pending_cleanup"]