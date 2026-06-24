"""知识库业务逻辑（V1.5 PRD §3.2 KB-01~05）。

设计要点：
- KB-01 同步：先建 Milvus Collection，再写 PG。任一步失败 → 整体回滚（PG 事务
  + Milvus drop 兜底），保证不产生孤儿资源
- KB-04 仅 name / description 可改；Schema 层已用 extra="forbid" 拦截其它字段
- KB-05 严格按 PRD §3.2 顺序：Milvus drop → PG delete → Neo4j delete
  - Milvus drop 失败 → 整体回滚返 500
  - Milvus drop 成功后 PG / Neo4j 失败 → 数据不一致只能记日志告警（Milvus 不可恢复）
- name 唯一冲突 → 抛 BusinessError(NAME_CONFLICT)
"""

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import desc, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import error_codes
from app.api.exceptions import BusinessError
from app.models.knowledge_base import (
    KB_STATUS_DELETING,
    KB_STATUS_PENDING_CLEANUP,
    KnowledgeBase,
)
from app.rag.milvus_client import (
    create_v2_kb_collection,
    drop_kb_collection,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ──────────────── 公共查询 ────────────────


async def get_kb_or_raise(
    db: AsyncSession, kb_id: uuid.UUID
) -> KnowledgeBase:
    """按 ID 取 KB；不存在抛 BusinessError(NOT_FOUND)。"""
    stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
    kb = (await db.execute(stmt)).scalar_one_or_none()
    if kb is None:
        raise BusinessError(error_codes.NOT_FOUND, f"知识库 {kb_id} 不存在")
    return kb


async def _kb_name_exists(
    db: AsyncSession, name: str, *, exclude_id: uuid.UUID | None = None
) -> bool:
    """检查 name 是否已被占用；exclude_id 用于 update 场景（不算自己）。"""
    stmt = select(KnowledgeBase.id).where(KnowledgeBase.name == name)
    if exclude_id is not None:
        stmt = stmt.where(KnowledgeBase.id != exclude_id)
    return (await db.execute(stmt)).first() is not None


# ──────────────── KB-01 创建 ────────────────


async def create_kb(
    db: AsyncSession,
    *,
    name: str,
    description: str | None = None,
    embedding_dim: int = 4096,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
) -> KnowledgeBase:
    """创建知识库（KB-01）。

    步骤：
    1. PG 查重 name（业务层提前 catch，给出明确错误码）
    2. 创建 Milvus Collection（失败直接抛，无副作用）
    3. PG 写入元数据
    4. 若 PG 写入失败 → 回滚 Milvus（drop_kb_collection）避免孤儿 collection

    Args:
        其他参数对齐 PRD KB-01 字段约束

    Raises:
        BusinessError(NAME_CONFLICT): name 重复
        BusinessError(INTERNAL_ERROR): Milvus 异常等
    """
    # 1) 提前查重，让 409 更早返回（DB 唯一索引兜底，并发场景靠 IntegrityError 兜底）
    if await _kb_name_exists(db, name):
        raise BusinessError(
            error_codes.NAME_CONFLICT, f"知识库名称 '{name}' 已存在"
        )

    # 2) 创建 V2 Milvus Collection（同步，失败直接抛 RuntimeError）
    # V2 Schema 含 heading_path / block_type / parent_chunk_id / is_summary /
    # sparse_vector 等 15 字段，与入库管道 _step_milvus_write_v2 对齐
    kb_id = uuid.uuid4()
    try:
        create_v2_kb_collection(kb_id, dim=embedding_dim)
    except RuntimeError as e:
        logger.error("KB-01 创建 Milvus Collection 失败 kb_id=%s: %s", kb_id, e)
        raise BusinessError(
            error_codes.INTERNAL_ERROR,
            f"创建知识库底层资源失败：{e}",
        ) from e

    # 3) 写 PG；任何异常都要回滚 Milvus
    kb = KnowledgeBase(
        id=kb_id,
        name=name,
        description=description,
        embedding_dim=embedding_dim,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    db.add(kb)
    try:
        await db.commit()
    except IntegrityError as e:
        # 并发场景：两个请求同时进来都过了 _kb_name_exists 检查
        await db.rollback()
        _safe_rollback_milvus(kb_id, reason="PG name 唯一冲突")
        raise BusinessError(
            error_codes.NAME_CONFLICT,
            f"知识库名称 '{name}' 已存在（并发冲突）",
        ) from e
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        _safe_rollback_milvus(kb_id, reason=f"PG 写入失败: {e}")
        raise BusinessError(
            error_codes.INTERNAL_ERROR,
            f"知识库元数据写入失败：{e}",
        ) from e

    await db.refresh(kb)
    logger.info(
        "KB-01 知识库创建成功 id=%s name=%r dim=%d", kb.id, kb.name, kb.embedding_dim
    )
    return kb


def _safe_rollback_milvus(kb_id: uuid.UUID, *, reason: str) -> None:
    """KB-01 失败时兜底删除 Milvus Collection；不抛错（避免遮蔽原始异常）。"""
    try:
        drop_kb_collection(kb_id)
        logger.warning(
            "KB-01 回滚 Milvus Collection 成功 kb_id=%s reason=%s", kb_id, reason
        )
    except Exception as drop_err:  # noqa: BLE001
        # 回滚失败：Milvus 里残留孤儿 Collection，需要人工清理
        logger.error(
            "KB-01 回滚 Milvus Collection 失败 kb_id=%s reason=%s drop_err=%s",
            kb_id,
            reason,
            drop_err,
        )


# ──────────────── KB-02 列表 ────────────────


MAX_PAGE_SIZE = 100


async def list_kbs(
    db: AsyncSession, page: int = 1, page_size: int = 20
) -> tuple[list[KnowledgeBase], int]:
    """分页返回知识库列表 + 总数；按 created_at 倒序。

    P1-9：默认隐藏 deleting / pending_cleanup 状态的 KB（用户视角已删除）。
    详情接口 get_kb_or_raise 仍能查到 pending_cleanup 行（便于运维诊断）。
    """
    page = max(page, 1)
    page_size = max(min(page_size, MAX_PAGE_SIZE), 1)

    # P1-9：过滤掉"逻辑已删但外存清理中/失败"的 KB
    visible_filter = KnowledgeBase.status.notin_(
        [KB_STATUS_DELETING, KB_STATUS_PENDING_CLEANUP]
    )

    total = (
        await db.execute(
            select(func.count()).select_from(KnowledgeBase).where(visible_filter)
        )
    ).scalar_one()

    items_stmt = (
        select(KnowledgeBase)
        .where(visible_filter)
        .order_by(desc(KnowledgeBase.created_at), desc(KnowledgeBase.id))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = list((await db.execute(items_stmt)).scalars().all())
    return items, total


# ──────────────── KB-04 更新 ────────────────


async def update_kb(
    db: AsyncSession,
    kb_id: uuid.UUID,
    *,
    name: str | None = None,
    description: str | None = None,
    description_was_set: bool = False,
    retrieval_config: dict | None = None,
    retrieval_config_was_set: bool = False,
) -> KnowledgeBase:
    """更新知识库字段（KB-04 + V2.0 HRE-06）。

    Args:
        name: 新名称；None 表示不改
        description: 新描述；None 含义看 description_was_set
        description_was_set: True = 用户显式传了 description（可能为 None，等价"清空"）
                             False = 用户未传 description 字段（保持原值）
        retrieval_config: V2.0 知识库级检索默认配置；含义看 retrieval_config_was_set
        retrieval_config_was_set: True = 用户显式传了 retrieval_config，按以下规则处理：
                                   - {} → 清空所有覆盖（写 None）
                                   - dict 非空 → 完整覆盖（不做 deep merge，调用方自己合并）
                                   - None → 清空（与 {} 等价）
                                  False = 用户未传该字段（保持原值）

    Raises:
        BusinessError(NAME_CONFLICT): 新 name 已被其它 KB 占用
        BusinessError(NOT_FOUND): kb_id 不存在
    """
    kb = await get_kb_or_raise(db, kb_id)

    if name is not None and name != kb.name:
        if await _kb_name_exists(db, name, exclude_id=kb_id):
            raise BusinessError(
                error_codes.NAME_CONFLICT,
                f"知识库名称 '{name}' 已被其它知识库占用",
            )
        kb.name = name

    if description_was_set:
        kb.description = description

    if retrieval_config_was_set:
        # 空 dict 或 None 都视为清空覆盖；非空 dict 直接整体写入
        kb.retrieval_config = retrieval_config if retrieval_config else None

    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise BusinessError(
            error_codes.NAME_CONFLICT,
            f"知识库名称 '{name}' 已存在（并发冲突）",
        ) from e

    await db.refresh(kb)
    logger.info("KB-04 知识库更新成功 id=%s name=%r", kb.id, kb.name)
    return kb


# ──────────────── KB-05 删除（PRD 严格顺序：Milvus → PG → Neo4j） ────────────────


async def delete_kb(db: AsyncSession, kb_id: uuid.UUID) -> None:
    """完全清理知识库的所有资源（KB-05 + V2 hardening P1-9）。

    P1-9 改造：把"Milvus 失败 → 整体回滚返 500"改成"失败降级为补偿"：

      1) revoke 所有进行中任务
      2) PG 状态改 deleting + commit
      3) 同步清外存：Milvus drop_collection + Neo4j DETACH DELETE
      4) 全 OK → PG 真删 + 磁盘清理
         任一失败 → status 改 pending_cleanup + commit（用户视角接口仍返 200）

    与 P1-9 之前的差异：
    - 不再向用户抛 500（Milvus 暂时不可用不是用户该关心的中断）
    - 外存清理失败时保留 PG 行，让 cleanup_reaper_task 后台兜底
    - PG 删除失败仍抛 500（这是真异常，不可能被补偿）
    """
    kb = await get_kb_or_raise(db, kb_id)

    # ---- 1) revoke 所有进行中的 Celery 任务 ----
    await _revoke_kb_processing_tasks(db, kb_id)

    # ---- 2) PG 标 deleting + commit ----
    kb.status = KB_STATUS_DELETING
    try:
        await db.commit()
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        raise BusinessError(
            error_codes.INTERNAL_ERROR, f"知识库状态切换失败：{e}"
        ) from e

    # ---- 3) 同步清外存 ----
    milvus_ok = _safe_drop_kb_collection(kb_id)
    neo4j_ok = await _cleanup_kb_neo4j(kb_id)

    # ---- 4) 分支：全成功 → PG 真删；任一失败 → 改 pending_cleanup ----
    if milvus_ok and neo4j_ok:
        try:
            await db.delete(kb)
            await db.commit()
        except Exception as e:  # noqa: BLE001
            await db.rollback()
            logger.error(
                "KB-05 PG 删除失败 kb_id=%s（Milvus/Neo4j 已清理，数据不一致）: %s",
                kb_id,
                e,
            )
            raise BusinessError(
                error_codes.INTERNAL_ERROR,
                f"知识库元数据删除失败（向量/图谱已清理，请人工介入）：{e}",
            ) from e

        _cleanup_kb_upload_dir(kb_id)
        logger.info("KB-05 知识库删除完成 kb_id=%s", kb_id)
    else:
        # 外存清理失败 → 留行让 reaper 接手
        from app.services.kb_file_service import _format_cleanup_failure_reason

        reason = _format_cleanup_failure_reason(
            milvus_ok=milvus_ok, neo4j_ok=neo4j_ok
        )
        await db.execute(
            update(KnowledgeBase)
            .where(KnowledgeBase.id == kb_id)
            .values(
                status=KB_STATUS_PENDING_CLEANUP,
                description=(
                    f"{kb.description or ''}\n"
                    f"[P1-9] {reason}"
                ),
            )
        )
        try:
            await db.commit()
        except Exception as e:  # noqa: BLE001
            await db.rollback()
            raise BusinessError(
                error_codes.INTERNAL_ERROR,
                f"知识库状态切换 pending_cleanup 失败：{e}",
            ) from e
        logger.warning(
            "KB-05 知识库外存清理失败已转 pending_cleanup kb_id=%s reason=%s",
            kb_id,
            reason,
        )


def _safe_drop_kb_collection(kb_id: uuid.UUID) -> bool:
    """P1-9 包装 drop_kb_collection 为 bool 返回。

    不抛异常，失败时返回 False 让调用方决策补偿路径。
    """
    try:
        drop_kb_collection(kb_id)
        return True
    except RuntimeError as e:
        logger.warning("KB-05 Milvus drop 失败 kb_id=%s: %s", kb_id, e)
        return False


async def _revoke_kb_processing_tasks(
    db: AsyncSession, kb_id: uuid.UUID
) -> None:
    """KB-05 第 1 步：revoke 该 KB 下所有 processing 状态文件的 Celery 任务。

    幂等：任务已结束 / 已 revoke / task_id 为空都不报错。
    """
    from app.models.kb_file import FILE_STATUS_PROCESSING, KbFile

    stmt = select(KbFile.id, KbFile.celery_task_id).where(
        KbFile.kb_id == kb_id,
        KbFile.status == FILE_STATUS_PROCESSING,
    )
    rows = list((await db.execute(stmt)).all())

    if not rows:
        logger.info("KB-05 无 processing 任务需 revoke kb_id=%s", kb_id)
        return

    try:
        from app.tasks.celery_app import celery_app

        for file_id, task_id in rows:
            if not task_id:
                continue
            try:
                celery_app.control.revoke(task_id, terminate=True)
                logger.info(
                    "KB-05 已 revoke 任务 kb_id=%s file_id=%s task_id=%s",
                    kb_id,
                    file_id,
                    task_id,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "KB-05 revoke 失败 file_id=%s task_id=%s err=%s",
                    file_id,
                    task_id,
                    e,
                )
    except ImportError:
        logger.warning("KB-05 Celery 不可用，跳过任务 revoke kb_id=%s", kb_id)


async def _cleanup_kb_neo4j(kb_id: uuid.UUID) -> bool:
    """KB-05 第 3 步：删除该 KB 在 Neo4j 中的所有节点和关系。

    粒度：DETACH DELETE 所有 (n {kb_id: $kb_id})；
    一并删 Document 和所有"只属于这个 KB"的 Entity。

    P1-9 改造：返 bool，True = 成功或可跳过，False = 真失败需要补偿
    （驱动不可用 / 未初始化属于"无外存可清理"的预期场景，返 True；
     真正连上 Neo4j 但 Cypher 执行炸了才返 False）
    """
    try:
        from app.core.config import get_settings
        from app.kg.neo4j_client import get_neo4j_driver
    except ImportError as e:
        logger.warning("KB-05 Neo4j 驱动不可用，跳过图谱清理: %s", e)
        return True

    try:
        driver = get_neo4j_driver()
    except RuntimeError as e:
        logger.warning("KB-05 Neo4j 未初始化，跳过图谱清理 kb_id=%s: %s", kb_id, e)
        return True

    settings = get_settings()
    cypher = "MATCH (n {kb_id: $kb_id}) DETACH DELETE n"
    try:
        async with driver.session(database=settings.neo4j_database) as sess:
            await sess.run(cypher, kb_id=str(kb_id))
        logger.info("KB-05 Neo4j 子图已清理 kb_id=%s", kb_id)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("KB-05 Neo4j 子图清理失败 kb_id=%s err=%s", kb_id, e)
        return False


def _cleanup_kb_upload_dir(kb_id: uuid.UUID) -> None:
    """KB-05 第 5 步：清空 {UPLOAD_DIR}/{kb_id}/ 目录树。

    复用 kb_file_service.remove_kb_upload_root；失败 warning 不抛。
    """
    try:
        from app.services.kb_file_service import remove_kb_upload_root

        remove_kb_upload_root(kb_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("KB-05 磁盘清理失败 kb_id=%s err=%s", kb_id, e)


# ──────────────── KB-03 entity_count 懒计算（S5 接通） ────────────────


async def count_entities_for_kb(kb_id: uuid.UUID) -> int:
    """查询指定 KB 在 Neo4j 中的 Entity 节点数（KB-03 详情用）。

    V1.5 S5：接通 Neo4j 真实查询（S2 阶段的 stub 已拆掉）。
    失败仅记 warning 返 0，不抛错（KB-03 详情接口不应因 Neo4j 抖动 500）。
    """
    try:
        from app.core.config import get_settings
        from app.kg.neo4j_client import get_neo4j_driver
    except ImportError as e:
        logger.warning("count_entities_for_kb: Neo4j 驱动不可用 → 0：%s", e)
        return 0

    try:
        driver = get_neo4j_driver()
    except RuntimeError as e:
        logger.warning(
            "count_entities_for_kb: Neo4j 未初始化 kb_id=%s → 0: %s", kb_id, e
        )
        return 0

    settings = get_settings()
    cypher = "MATCH (e:Entity {kb_id: $kb_id}) RETURN count(e) AS n"
    try:
        async with driver.session(database=settings.neo4j_database) as sess:
            result = await sess.run(cypher, kb_id=str(kb_id))
            rec = await result.single()
            return int(rec["n"]) if rec else 0
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "count_entities_for_kb: 查询失败 kb_id=%s err=%s → 0", kb_id, e
        )
        return 0
