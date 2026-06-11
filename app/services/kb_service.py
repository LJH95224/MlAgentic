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

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import error_codes
from app.api.exceptions import BusinessError
from app.models.knowledge_base import KnowledgeBase
from app.rag.milvus_client import (
    create_kb_collection,
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

    # 2) 创建 Milvus Collection（同步，失败直接抛 RuntimeError）
    kb_id = uuid.uuid4()
    try:
        create_kb_collection(kb_id, dim=embedding_dim)
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
    except Exception as e:
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
    """分页返回知识库列表 + 总数；按 created_at 倒序。"""
    page = max(page, 1)
    page_size = max(min(page_size, MAX_PAGE_SIZE), 1)

    total = (
        await db.execute(select(func.count()).select_from(KnowledgeBase))
    ).scalar_one()

    items_stmt = (
        select(KnowledgeBase)
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
) -> KnowledgeBase:
    """更新知识库 name / description（KB-04）。

    Args:
        name: 新名称；None 表示不改
        description: 新描述；None 含义看 description_was_set
        description_was_set: True = 用户显式传了 description（可能为 None，等价"清空"）
                             False = 用户未传 description 字段（保持原值）

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
    """完全清理知识库的所有资源（KB-05）。

    严格按 PRD §3.2 KB-05 顺序：
    1. Milvus drop_collection（不可逆，最先做；失败 → 整体回滚返 500）
    2. PG 删 knowledge_bases 记录（外键级联删 kb_files；失败 → Milvus 已丢，
       记日志告警，仍向上抛 500）
    3. Neo4j MATCH (n {kb_id}) DETACH DELETE n（S5 阶段接通；S2 stub 仅记日志）

    数据不一致风险：
    - Milvus 成功 + PG 失败 → 用户 GET 仍能看到 KB（PG 在），但里头检索全空
      → 重试 DELETE 会再次 Milvus drop（幂等返 False）+ 再次尝试 PG delete
    - PG 成功 + Neo4j 失败 → Neo4j 残留 kb_id 节点，不影响业务，运维补刀

    业务层做二次确认的责任在调用方（前端弹窗）。
    """
    kb = await get_kb_or_raise(db, kb_id)

    # ---- 1) Milvus drop（PRD 要求最先，不可逆）----
    try:
        drop_kb_collection(kb_id)
    except RuntimeError as e:
        logger.error("KB-05 Milvus drop 失败 kb_id=%s: %s", kb_id, e)
        raise BusinessError(
            error_codes.INTERNAL_ERROR,
            f"删除知识库底层向量资源失败：{e}",
        ) from e

    # ---- 2) PG delete（外键 ondelete=CASCADE 自动级联删 kb_files）----
    try:
        await db.delete(kb)
        await db.commit()
    except Exception as e:
        await db.rollback()
        # Milvus 已删但 PG 删除失败 —— 数据不一致，告警让人工介入
        logger.error(
            "KB-05 PG 删除失败 kb_id=%s（Milvus Collection 已 drop，数据不一致）: %s",
            kb_id,
            e,
        )
        raise BusinessError(
            error_codes.INTERNAL_ERROR,
            f"知识库元数据删除失败（向量库已清理，请人工介入）：{e}",
        ) from e

    # ---- 3) Neo4j 清理（S5 阶段接通；当前 stub 仅记日志）----
    # 留 TODO 标记：S5 实现时取消注释下两行 + 引入 app.kg.writer 的 delete_kb_subgraph
    # try:
    #     await delete_kb_subgraph(kb_id)
    # except Exception as e:
    #     logger.error("KB-05 Neo4j 子图删除失败 kb_id=%s（Milvus+PG 已删，仅图谱残留）: %s", kb_id, e)
    logger.info("KB-05 知识库删除完成 kb_id=%s（Neo4j 清理待 S5 接通）", kb_id)


# ──────────────── KB-03 entity_count 懒计算（S5 接通） ────────────────


async def count_entities_for_kb(kb_id: uuid.UUID) -> int:
    """查询指定 KB 在 Neo4j 中的 Entity 节点数（KB-03 详情用）。

    S2 阶段 stub 返回 0；S5 阶段（KB 关联对话）接通 Neo4j 时改为实查。
    保持函数签名独立，调用方（endpoint）无需感知变化。
    """
    # S5 实现示例（参考）：
    # from app.kg.query import count_entities_by_kb
    # return await count_entities_by_kb(kb_id)
    return 0
