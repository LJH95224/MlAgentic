"""KB 文件业务逻辑（V1.5 PRD §3.3 FILE-01~05）。

设计要点：
- FILE-01 上传：边读边量 size（防 Content-Length 欺骗）→ 写盘 → PG 写元数据
  → KnowledgeBase.file_count += 1 → 触发 Celery 任务 → 把 task_id 写回 → 立即返
- 允许同名文件：磁盘路径用 file_id 隔离 `{UPLOAD_DIR}/{kb_id}/{file_id}/{filename}`
- 文件大小校验在 service 层：用 SpooledTemporaryFile + chunk 读，超限立即抛
- FILE-04 / FILE-05 删除/重建：先 revoke Celery 任务 → Milvus → Neo4j → PG → 磁盘
  - S3.1 阶段 Milvus / Neo4j 清理走 TODO（S3.2 / S5 接通），先实现 PG + 磁盘
"""

import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import error_codes
from app.api.exceptions import BusinessError
from app.core.config import get_settings
from app.ingest.parser import (
    check_mime_compatibility,
    is_supported_filename,
)
from app.models.kb_file import (
    FILE_STATUS_FAILED,
    FILE_STATUS_PENDING,
    FILE_STATUS_PROCESSING,
    KbFile,
)
from app.models.knowledge_base import KnowledgeBase
from app.services.kb_service import get_kb_or_raise

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# 列表分页上限（同 KB-02 / SES-02）
MAX_PAGE_SIZE = 100

# 读盘 chunk 大小：4MB，平衡内存与 syscall 次数
_READ_CHUNK_BYTES = 4 * 1024 * 1024


# ──────────────── 公共查询 ────────────────


async def get_file_or_raise(
    db: AsyncSession, kb_id: uuid.UUID, file_id: uuid.UUID
) -> KbFile:
    """按 (kb_id, file_id) 取文件元数据；不存在抛 404。

    强制带 kb_id 校验，防止跨 KB 越权访问（业务隔离基线）。
    """
    stmt = select(KbFile).where(
        KbFile.id == file_id, KbFile.kb_id == kb_id
    )
    f = (await db.execute(stmt)).scalar_one_or_none()
    if f is None:
        raise BusinessError(
            error_codes.NOT_FOUND,
            f"文件 {file_id} 在知识库 {kb_id} 下不存在",
        )
    return f


# ──────────────── FILE-01 上传 ────────────────


def _build_storage_path(kb_id: uuid.UUID, file_id: uuid.UUID, filename: str) -> Path:
    """生成磁盘存储路径：{UPLOAD_DIR}/{kb_id}/{file_id}/{filename}。

    file_id 做主隔离 → 同名文件天然分目录，无冲突。
    """
    settings = get_settings()
    return Path(settings.upload_dir) / str(kb_id) / str(file_id) / filename


def _save_upload_streaming(
    src_stream: BinaryIO, dst_path: Path, *, size_limit_bytes: int
) -> int:
    """边读边写，期间累计 size；超限立即抛并删半成品。

    返回最终写入字节数。**只信流读到的字节数**，不信 Content-Length。
    """
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with open(dst_path, "wb") as out:
            while True:
                chunk = src_stream.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > size_limit_bytes:
                    out.close()
                    dst_path.unlink(missing_ok=True)
                    raise BusinessError(
                        error_codes.FILE_TOO_LARGE,
                        f"文件大小超出限制 {size_limit_bytes // (1024 * 1024)} MB",
                    )
                out.write(chunk)
    except BusinessError:
        raise
    except Exception as e:
        # IO 失败：清掉半成品防垃圾
        dst_path.unlink(missing_ok=True)
        raise BusinessError(
            error_codes.INTERNAL_ERROR, f"文件保存失败：{e}"
        ) from e

    return total


async def upload_file(
    db: AsyncSession,
    kb_id: uuid.UUID,
    *,
    src_stream: BinaryIO,
    filename: str,
    declared_mime: str | None,
) -> KbFile:
    """上传文件 + 同步写元数据 + 触发异步入库任务（FILE-01）。

    步骤：
      1. 校验 KB 存在
      2. 校验扩展名在白名单（不在 → 415）；MIME 不匹配 → warning 不抛
      3. 生成 file_id + 磁盘路径
      4. 边读边写到磁盘（超过 MAX_FILE_SIZE_MB → 413 + 删半成品）
      5. PG 写 kb_files 行（status=pending, progress=0）+ KB.file_count +=1
      6. 触发 Celery 任务 parse_and_ingest_task(file_id, kb_id)
      7. 把 task_id 写回 kb_files.celery_task_id
      8. 返回 KbFile（不等入库完成）

    Raises:
        BusinessError(NOT_FOUND): KB 不存在
        BusinessError(UNSUPPORTED_MEDIA): 扩展名不在白名单
        BusinessError(FILE_TOO_LARGE): 文件超限
        BusinessError(INTERNAL_ERROR): 磁盘 IO / PG 写入失败
    """
    settings = get_settings()
    size_limit = settings.max_file_size_mb * 1024 * 1024

    # 1) KB 必须存在
    kb = await get_kb_or_raise(db, kb_id)

    # 2) 扩展名校验（PRD S3.7 决策）
    if not is_supported_filename(filename):
        raise BusinessError(
            error_codes.UNSUPPORTED_MEDIA,
            f"不支持的文件格式: {filename}（V1.5 仅支持 .pdf / .docx / .md / .txt）",
        )
    # MIME 二次校验：不匹配仅 warning 不阻断
    if not check_mime_compatibility(filename, declared_mime):
        logger.warning(
            "文件 MIME 与扩展名不匹配（仍放行）: filename=%s declared_mime=%s",
            filename,
            declared_mime,
        )

    # 3) 生成 file_id + 路径
    file_id = uuid.uuid4()
    dst_path = _build_storage_path(kb_id, file_id, filename)

    # 4) 写磁盘（边读边量 size）
    file_size = _save_upload_streaming(src_stream, dst_path, size_limit_bytes=size_limit)

    # 5) PG 写元数据 + KB 冗余计数 +1
    kb_file = KbFile(
        id=file_id,
        kb_id=kb_id,
        filename=filename,
        file_path=str(dst_path),
        file_size=file_size,
        mime_type=declared_mime or "application/octet-stream",
        status=FILE_STATUS_PENDING,
        progress=0,
    )
    db.add(kb_file)

    # 同事务内 KB.file_count += 1（原子表达式，并发安全）
    await db.execute(
        update(KnowledgeBase)
        .where(KnowledgeBase.id == kb_id)
        .values(file_count=KnowledgeBase.file_count + 1)
    )

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        # PG 失败 → 清磁盘文件防垃圾
        dst_path.unlink(missing_ok=True)
        raise BusinessError(
            error_codes.INTERNAL_ERROR, f"文件元数据写入失败：{e}"
        ) from e
    await db.refresh(kb_file)

    # 6) 触发 Celery 任务（在 db.commit 之后调，保证 worker 拿到的 file_id 已落库）
    # 局部导入：避免 Celery 不可用时影响 service 模块加载
    try:
        from app.tasks.ingest_task import parse_and_ingest_task

        async_result = parse_and_ingest_task.delay(str(file_id), str(kb_id))
        task_id = async_result.id
    except Exception as e:  # noqa: BLE001
        # Celery 不可达：不阻断上传，任务用 reindex 接口补救（FILE-05）
        logger.error(
            "FILE-01 触发 Celery 任务失败 file_id=%s kb_id=%s: %s",
            file_id,
            kb_id,
            e,
        )
        task_id = None

    # 7) 把 task_id 写回（即使为 None 也要走一次 UPDATE，让事务结构清晰）
    if task_id is not None:
        await db.execute(
            update(KbFile)
            .where(KbFile.id == file_id)
            .values(celery_task_id=task_id)
        )
        await db.commit()
        await db.refresh(kb_file)

    logger.info(
        "FILE-01 文件上传成功 file_id=%s kb_id=%s filename=%r size=%d task_id=%s",
        file_id,
        kb_id,
        filename,
        file_size,
        task_id,
    )
    return kb_file


# ──────────────── FILE-02 列表 ────────────────


async def list_kb_files(
    db: AsyncSession,
    kb_id: uuid.UUID,
    *,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[KbFile], int]:
    """分页返回 KB 下的文件列表 + 总数；按 created_at 倒序。

    会先校验 KB 存在（FILE-02 验收：KB 不存在 → 404）。
    """
    await get_kb_or_raise(db, kb_id)
    page = max(page, 1)
    page_size = max(min(page_size, MAX_PAGE_SIZE), 1)

    total = (
        await db.execute(
            select(func.count())
            .select_from(KbFile)
            .where(KbFile.kb_id == kb_id)
        )
    ).scalar_one()

    items_stmt = (
        select(KbFile)
        .where(KbFile.kb_id == kb_id)
        .order_by(desc(KbFile.created_at), desc(KbFile.id))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = list((await db.execute(items_stmt)).scalars().all())
    return items, total


# ──────────────── FILE-04 删除 / FILE-05 重建 ────────────────


def _safe_revoke_task(task_id: str | None) -> None:
    """如果文件状态 processing，先 revoke Celery 任务。

    Celery 不可达时只记 warning（任务最终会在 worker 启动后被发现已 revoke，
    或者超时被 worker 自然清理）。
    """
    if not task_id:
        return
    try:
        from app.tasks.celery_app import celery_app

        celery_app.control.revoke(task_id, terminate=True)
        logger.info("Celery 任务已 revoke task_id=%s", task_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Celery revoke 失败（已忽略）task_id=%s err=%s", task_id, e
        )


def _safe_remove_disk(file_path: str) -> None:
    """删除磁盘文件 + 空目录（按 {kb_id}/{file_id}/{filename} 三层结构）。

    幂等：不存在不报错；权限错误记 warning 不阻断流程。
    """
    p = Path(file_path)
    try:
        if p.exists():
            p.unlink()
        # 删空目录 {file_id}/，不动 {kb_id}/（其他文件可能还在）
        parent = p.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError as e:
        logger.warning("磁盘文件删除失败 path=%s err=%s", file_path, e)


async def _cleanup_milvus_chunks_for_file(
    kb_id: uuid.UUID, file_id: uuid.UUID
) -> None:
    """删除 Milvus 中该文件的所有切片（按 document_id == file_id 过滤）。

    走全局 Milvus client（init_milvus 已在 FastAPI lifespan 初始化）。
    Collection 不存在或 delete 失败仅记 warning，不阻断主清理流程
    （目标是把能删的尽量删干净）。
    """
    try:
        from app.rag.milvus_client import get_milvus_client
        from app.rag.naming import build_kb_collection_name
    except ImportError as e:
        logger.error("Milvus 客户端不可用，跳过切片清理: %s", e)
        return

    collection = build_kb_collection_name(kb_id)
    try:
        client = get_milvus_client()
    except RuntimeError as e:
        # init_milvus 未跑过（脚本场景）；记日志放行
        logger.warning("Milvus 未初始化，跳过切片清理 kb_id=%s: %s", kb_id, e)
        return

    if not client.has_collection(collection):
        logger.info(
            "Milvus Collection 不存在 collection=%s file_id=%s（可能 KB 刚 drop）",
            collection,
            file_id,
        )
        return

    try:
        # filter 表达式按 document_id 精确匹配；与 ingest_task 写入时的字段对齐
        client.delete(
            collection_name=collection,
            filter=f'document_id == "{file_id}"',
        )
        logger.info(
            "Milvus 切片已清理 collection=%s file_id=%s", collection, file_id
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Milvus 切片清理失败 collection=%s file_id=%s err=%s",
            collection,
            file_id,
            e,
        )


async def _cleanup_neo4j_entities_for_file(
    kb_id: uuid.UUID, file_id: uuid.UUID
) -> None:
    """删除 Neo4j 中该文件锚定的 Document + 它相关的 MENTIONED_IN 关系。

    Entity 节点本身不删（同一实体可能被多个文档引用）；只删本 file 的关系和
    Document 节点。Document 用 (document_id, kb_id) 二维匹配防误删。

    异常时仅记 warning，不阻断主清理流程。
    """
    try:
        from app.core.config import get_settings
        from app.kg.neo4j_client import get_neo4j_driver
    except ImportError as e:
        logger.error("Neo4j 驱动不可用，跳过实体清理: %s", e)
        return

    try:
        driver = get_neo4j_driver()
    except RuntimeError as e:
        logger.warning("Neo4j 未初始化，跳过实体清理 kb_id=%s: %s", kb_id, e)
        return

    settings = get_settings()
    document_id = str(file_id)
    kb_id_str = str(kb_id)

    # 1) 删 Document 节点及其所有出入关系（DETACH DELETE）
    # 2) 该 Document 关联的 Entity 节点不动（保持复用）
    delete_cypher = """
    MATCH (d:Document {document_id: $document_id, kb_id: $kb_id})
    DETACH DELETE d
    """.strip()

    try:
        async with driver.session(database=settings.neo4j_database) as sess:
            await sess.run(
                delete_cypher,
                document_id=document_id,
                kb_id=kb_id_str,
            )
        logger.info(
            "Neo4j Document 节点已清理 kb_id=%s file_id=%s",
            kb_id,
            file_id,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Neo4j 实体清理失败 kb_id=%s file_id=%s err=%s",
            kb_id,
            file_id,
            e,
        )


async def delete_file(
    db: AsyncSession, kb_id: uuid.UUID, file_id: uuid.UUID
) -> None:
    """删除文件及相关资源（FILE-04）。

    顺序（PRD §3.3 FILE-04）：
      1. 若 status=processing → revoke Celery 任务
      2. Milvus 删切片（S3.2 接通）
      3. Neo4j 删实体（S5 接通）
      4. PG 删 kb_files + KB.file_count -=1 + chunk_count -= old_chunk_count
      5. 磁盘删原始文件 + 空目录
    """
    kb_file = await get_file_or_raise(db, kb_id, file_id)

    # 1) revoke（仅 processing 状态需要）
    if kb_file.status == FILE_STATUS_PROCESSING:
        _safe_revoke_task(kb_file.celery_task_id)

    # 2) Milvus / 3) Neo4j 清理（S3.2 / S5 stub）
    await _cleanup_milvus_chunks_for_file(kb_id, file_id)
    await _cleanup_neo4j_entities_for_file(kb_id, file_id)

    # 4) PG：删 file 行 + KB 计数减回去
    old_chunk_count = kb_file.chunk_count
    file_path = kb_file.file_path

    await db.delete(kb_file)
    await db.execute(
        update(KnowledgeBase)
        .where(KnowledgeBase.id == kb_id)
        .values(
            file_count=KnowledgeBase.file_count - 1,
            chunk_count=KnowledgeBase.chunk_count - old_chunk_count,
        )
    )
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise BusinessError(
            error_codes.INTERNAL_ERROR, f"文件元数据删除失败：{e}"
        ) from e

    # 5) 磁盘
    _safe_remove_disk(file_path)

    logger.info(
        "FILE-04 文件删除完成 kb_id=%s file_id=%s 释放切片=%d",
        kb_id,
        file_id,
        old_chunk_count,
    )


async def reindex_file(
    db: AsyncSession, kb_id: uuid.UUID, file_id: uuid.UUID
) -> KbFile:
    """文件重新入库（FILE-05）。

    步骤：
      1. 验证文件 + 磁盘文件存在；磁盘文件丢失 → 404 提示重新上传
      2. revoke 旧任务（若 processing）
      3. Milvus 删旧切片 / Neo4j 删旧实体（S3.2 / S5 接通）
      4. KB.chunk_count -= old_chunk_count（重置计数）
      5. PG kb_files 状态重置：status=pending, progress=0, chunk_count=0,
         entity_count=0, error_message=None, completed_at=None
      6. 触发新 Celery 任务，写回新 task_id
    """
    kb_file = await get_file_or_raise(db, kb_id, file_id)

    # 1) 磁盘必须在；FILE-05 验收：磁盘文件不存在 → 404 提示重新上传
    if not os.path.exists(kb_file.file_path):
        raise BusinessError(
            error_codes.NOT_FOUND,
            f"文件磁盘副本已丢失，请重新上传 file_id={file_id}",
        )

    # 2) revoke 旧任务
    if kb_file.status == FILE_STATUS_PROCESSING:
        _safe_revoke_task(kb_file.celery_task_id)

    # 3) 清理 Milvus + Neo4j 旧切片
    await _cleanup_milvus_chunks_for_file(kb_id, file_id)
    await _cleanup_neo4j_entities_for_file(kb_id, file_id)

    # 4) KB chunk_count 减回去
    old_chunk_count = kb_file.chunk_count
    if old_chunk_count > 0:
        await db.execute(
            update(KnowledgeBase)
            .where(KnowledgeBase.id == kb_id)
            .values(chunk_count=KnowledgeBase.chunk_count - old_chunk_count)
        )

    # 5) 重置 kb_files 状态
    kb_file.status = FILE_STATUS_PENDING
    kb_file.progress = 0
    kb_file.chunk_count = 0
    kb_file.entity_count = 0
    kb_file.error_message = None
    kb_file.completed_at = None
    kb_file.celery_task_id = None

    await db.commit()
    await db.refresh(kb_file)

    # 6) 触发新任务
    try:
        from app.tasks.ingest_task import parse_and_ingest_task

        async_result = parse_and_ingest_task.delay(str(file_id), str(kb_id))
        kb_file.celery_task_id = async_result.id
        await db.commit()
        await db.refresh(kb_file)
    except Exception as e:  # noqa: BLE001
        logger.error(
            "FILE-05 重建任务触发失败 file_id=%s: %s",
            file_id,
            e,
        )

    logger.info("FILE-05 文件重建任务已提交 kb_id=%s file_id=%s", kb_id, file_id)
    return kb_file


# ──────────────── 入库管道维护 KB 冗余计数（S3.2 入库任务用） ────────────────


async def _bump_kb_chunk_count(
    db: AsyncSession, kb_id: uuid.UUID, delta: int
) -> None:
    """供 S3.2 入库任务调：原子地把 KB.chunk_count += delta。

    入库任务完成时 delta=+new_chunks；FILE-04 删除时 delta=-old_chunks。
    """
    if delta == 0:
        return
    await db.execute(
        update(KnowledgeBase)
        .where(KnowledgeBase.id == kb_id)
        .values(chunk_count=KnowledgeBase.chunk_count + delta)
    )
    await db.commit()


async def mark_file_failed(
    db: AsyncSession, file_id: uuid.UUID, error_message: str
) -> None:
    """供 S3.2 入库任务异常路径调；不抛错。"""
    try:
        await db.execute(
            update(KbFile)
            .where(KbFile.id == file_id)
            .values(status=FILE_STATUS_FAILED, error_message=error_message[:2000])
        )
        await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.error("mark_file_failed 失败 file_id=%s: %s", file_id, e)


# ──────────────── 清理工具（FILE-05 重建可能用到） ────────────────


def remove_kb_upload_root(kb_id: uuid.UUID) -> None:
    """KB-05 删除知识库时调；清空该 KB 下所有上传文件目录。

    S2 阶段还没调它（KB-05 只删 PG + Milvus），等 S3 上传真有文件后再在
    kb_service.delete_kb 末尾追加这一步。
    """
    settings = get_settings()
    root = Path(settings.upload_dir) / str(kb_id)
    if root.exists():
        try:
            shutil.rmtree(root, ignore_errors=True)
            logger.info("KB 上传目录已清理 kb_id=%s path=%s", kb_id, root)
        except OSError as e:
            logger.warning("KB 上传目录清理失败 kb_id=%s err=%s", kb_id, e)
