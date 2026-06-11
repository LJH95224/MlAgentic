"""文件入库 Celery 任务（V1.5 PRD §3.4 TASK-02 / TASK-03）。

【架构约定 - dev_plan S3.5】
- Celery @task 用同步 def；核心 async def _main；体内只调一次 asyncio.run
- 所有外部连接（PG / Milvus / Neo4j）由 task_resources() 在 _main 入口现建、退出时 dispose
- 不依赖 app.main 全局单例（worker 进程无 lifespan）

【PRD §3.4 TASK-02 七步管道】
    Step 1: status=processing, progress=0       任务入口
    Step 2: 解析磁盘文件 → 纯文本               progress=20
    Step 3: RecursiveCharacterTextSplitter 切片  progress=35
    Step 4: 批量 embedding → 向量               progress=60
    Step 5: 批量写入 Milvus                     progress=80
    Step 6: 按 chunk LLM NER（软失败）           progress=90
    Step 7: 写 Neo4j Document + Entity + MENTIONED_IN  progress=95
    Step 8: status=completed, completed_at, KB 计数  progress=100

【PRD §3.4 TASK-03 重试策略】
- autoretry_for=(MilvusException, RedisConnectionError)：临时性网络抖动自动重试
- max_retries=3, countdown=30, retry_backoff=True：30s → 60s → 120s
- 3 次仍失败 → 最终 status=failed
- 非可重试异常（参数错、维度不匹配、磁盘文件丢失）直接 status=failed，不重试
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import traceback
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from app.core.config import get_settings
from app.ingest.parser import ParseError, parse_document
from app.ingest.splitter import Chunk, split_text
from app.kg.writer import (
    bulk_link_entities_to_chunk,
    bulk_upsert_entities,
    upsert_document,
)
from app.models.kb_file import (
    FILE_STATUS_COMPLETED,
    FILE_STATUS_FAILED,
    FILE_STATUS_PROCESSING,
    KbFile,
)
from app.models.knowledge_base import KnowledgeBase
from app.rag.embedding import aembed_texts
from app.rag.milvus_client import create_kb_collection
from app.rag.naming import build_kb_collection_name
from app.tasks._resources import TaskResources, task_resources
from app.tasks.celery_app import celery_app

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ──────────────── 常量 ────────────────


# 七步管道的 progress 锚点（与 PRD §3.4 TASK-02 表对齐）
PROGRESS_PARSED = 20
PROGRESS_SPLIT = 35
PROGRESS_EMBEDDED = 60
PROGRESS_MILVUS = 80
PROGRESS_NER = 90
PROGRESS_NEO4J = 95
PROGRESS_DONE = 100

# Embedding 批大小（PRD §6.2 default=32；避免单次请求超 API 限制）
EMBEDDING_BATCH_SIZE = 32

# Milvus 批写入大小（PRD §3.4 TASK-02 给的 50）
MILVUS_BATCH_SIZE = 50

# NER 并发限制（避免 LLM 限流冲垮整批入库）
# 8 并发 + 单 chunk 25s 硬超时：100 chunks 最坏 100/8*25=312s，5 分钟内完成
NER_CONCURRENCY = 8
# 单 chunk NER 最长 25 秒（litellm 默认 60s 太宽松，气象论文一份 50-200 chunk
# 会被一两个慢调用拖死整批）；超时按 NER 软失败原则返 []，不阻断
NER_SINGLE_TIMEOUT_SECONDS = 25

# Milvus 字段长度上限（与 app/rag/schema.py 中的常量对齐；防御性截断用）
# ⚠️ Milvus VARCHAR `max_length` 按 **UTF-8 字节数** 算，不是字符数！
#    一个中文字符 = 3 字节，所以 64 字节最多容纳约 21 个中文字符。
# 单条 entity_tag 最长 64 字节
_MAX_ENTITY_TAG_BYTES = 64
# entity_tags 数组最长 50 条
_MAX_ENTITY_TAGS_PER_CHUNK = 50
# content 字段 max_length=65535 字节
_MAX_CONTENT_BYTES = 65535


def _truncate_utf8(s: str, max_bytes: int) -> str:
    """按 UTF-8 字节数安全截断；不切断多字节字符。

    Milvus VARCHAR 的 max_length 按字节算（非字符数），中文场景必须用字节
    截断，否则像 22 字中文 = 66 字节 > 64 字节 max_length 会写失败。
    """
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    # 截断到 max_bytes，然后用 'ignore' 把可能被劈开的多字节字符末尾抛弃
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


# ──────────────── 工具函数 ────────────────


def _make_chunk_id(document_id: str, chunk_index: int) -> int:
    """生成稳定 chunk_id（INT64 正数）= hash(document_id + chunk_index)。

    与 scripts/rag_ingest.py 同款策略，保证 upsert 幂等。
    """
    key = f"{document_id}::{chunk_index}".encode("utf-8")
    h = hashlib.sha256(key).digest()
    raw = int.from_bytes(h[:8], byteorder="big", signed=False)
    return raw & 0x7FFF_FFF_FFFF_FFFF  # 取低 63 位


def _utc_now_iso() -> str:
    """ISO 8601 UTC 时间戳；用于 Document.created_at 节点属性。"""
    return datetime.now(timezone.utc).isoformat()


async def _set_progress(
    resources: TaskResources,
    file_id: uuid.UUID,
    *,
    progress: int,
    status: str | None = None,
    chunk_count: int | None = None,
    entity_count: int | None = None,
    completed_at: datetime | None = None,
    error_message: str | None = None,
) -> None:
    """更新 kb_files 行的进度字段。每个 step 末尾调一次。"""
    values: dict = {"progress": progress}
    if status is not None:
        values["status"] = status
    if chunk_count is not None:
        values["chunk_count"] = chunk_count
    if entity_count is not None:
        values["entity_count"] = entity_count
    if completed_at is not None:
        values["completed_at"] = completed_at
    if error_message is not None:
        # 截断防超 PG Text 索引等限制
        values["error_message"] = error_message[:2000]

    async with resources.db() as session:
        await session.execute(
            update(KbFile).where(KbFile.id == file_id).values(**values)
        )
        await session.commit()


async def _load_file_record(
    resources: TaskResources, file_id: uuid.UUID
) -> tuple[KbFile, KnowledgeBase]:
    """加载文件 + 关联 KB；任一不存在抛 ValueError（非可重试）。"""
    async with resources.db() as session:
        f = (
            await session.execute(select(KbFile).where(KbFile.id == file_id))
        ).scalar_one_or_none()
        if f is None:
            raise ValueError(f"file_id={file_id} 不存在")

        kb = (
            await session.execute(
                select(KnowledgeBase).where(KnowledgeBase.id == f.kb_id)
            )
        ).scalar_one_or_none()
        if kb is None:
            raise ValueError(f"file_id={file_id} 对应的 kb_id={f.kb_id} 不存在")

        # 把 ORM 对象与 session 解绑，让外层使用时不触发懒加载
        session.expunge(f)
        session.expunge(kb)
    return f, kb


async def _bump_kb_chunk_count(
    resources: TaskResources, kb_id: uuid.UUID, delta: int
) -> None:
    """原子地把 KB.chunk_count += delta（delta 可负）。"""
    if delta == 0:
        return
    async with resources.db() as session:
        await session.execute(
            update(KnowledgeBase)
            .where(KnowledgeBase.id == kb_id)
            .values(chunk_count=KnowledgeBase.chunk_count + delta)
        )
        await session.commit()


# ──────────────── 七步管道 ────────────────


async def _step_parse(file_record: KbFile) -> str:
    """Step 2: 解析磁盘文件 → 纯文本。"""
    text = parse_document(file_record.file_path, filename=file_record.filename)
    if not text.strip():
        raise ParseError(
            f"文件解析后内容为空 file_id={file_record.id} path={file_record.file_path}"
        )
    return text


def _step_split(text: str, kb: KnowledgeBase) -> list[Chunk]:
    """Step 3: 按 KB 配置切片。"""
    return split_text(
        text,
        chunk_size=kb.chunk_size,
        chunk_overlap=kb.chunk_overlap,
    )


async def _step_embed(chunks: list[Chunk]) -> list[list[float]]:
    """Step 4: 批量 embedding（按 batch_size 切分调用）。

    维度校验由 aembed_texts() 内部完成；维度不匹配 → ValueError → 任务 failed。
    """
    vectors: list[list[float]] = []
    for i in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
        batch = chunks[i : i + EMBEDDING_BATCH_SIZE]
        texts = [c.text for c in batch]
        batch_vecs = await aembed_texts(texts)
        vectors.extend(batch_vecs)
    return vectors


def _step_milvus_write(
    resources: TaskResources,
    *,
    kb: KnowledgeBase,
    file_record: KbFile,
    chunks: list[Chunk],
    vectors: list[list[float]],
    chunk_entities: list[list[dict]] | None = None,
) -> None:
    """Step 5: 批量写入 KB 对应的 Milvus Collection。

    chunk_entities：每个 chunk 对应的实体列表（NER 后注入 entity_tags）。
    若 NER 先做（Step 6 在 Step 5 之后），此处先用空列表，等 Step 6 补一次 upsert。

    PRD §6.3 NER 软失败原则：embedding/Milvus 不应等待 NER，因此 NER 实际是在
    Milvus 写入之后跑（PRD §3.4 TASK-02 步骤 Step 5=80 / Step 6=90，顺序就是这样）。
    """
    settings = get_settings()
    collection_name = build_kb_collection_name(kb.id)
    document_id = str(file_record.id)

    # 防御性：确保 collection 存在（FILE-05 reindex 之前可能刚被 drop）
    # 实际上 KB-01 已经建好，这里只为 self-healing
    if not resources.milvus.has_collection(collection_name):
        logger.warning(
            "Collection %s 不存在，尝试自愈创建（来自 KB.id=%s dim=%d）",
            collection_name,
            kb.id,
            kb.embedding_dim,
        )
        # 临时把 milvus_client._client 指向当前 task 的 client，让 create_kb_collection 用
        import app.rag.milvus_client as mod

        prev_client = mod._client
        mod._client = resources.milvus
        try:
            create_kb_collection(kb.id, dim=kb.embedding_dim)
        finally:
            mod._client = prev_client

    rows: list[dict] = []
    for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
        entity_tags: list[str] = []
        if chunk_entities is not None and i < len(chunk_entities):
            # 防御性清洗：
            #   1) 单 tag 按 UTF-8 字节数截断到 _MAX_ENTITY_TAG_BYTES
            #      （Milvus VARCHAR 是按字节算长度，中文 3 字节/字必须按字节判）
            #   2) 去重（同一 chunk 内 name 可能重复）
            #   3) 数组长度截断到 _MAX_ENTITY_TAGS_PER_CHUNK
            seen: set[str] = set()
            for e in chunk_entities[i]:
                name = (e.get("name") or "").strip()
                if not name:
                    continue
                truncated = _truncate_utf8(name, _MAX_ENTITY_TAG_BYTES)
                if truncated != name:
                    logger.warning(
                        "实体名超长被截断 file_id=%s chunk_index=%d 原长=%d 字节 → %d 字符",
                        file_record.id,
                        chunk.index,
                        len(name.encode("utf-8")),
                        len(truncated),
                    )
                if truncated in seen:
                    continue
                seen.add(truncated)
                entity_tags.append(truncated)
                if len(entity_tags) >= _MAX_ENTITY_TAGS_PER_CHUNK:
                    break

        # content 字段防御性截断（按 UTF-8 字节，防 split_text 个别 chunk 超长）
        content = _truncate_utf8(chunk.text, _MAX_CONTENT_BYTES)
        if content != chunk.text:
            logger.warning(
                "切片内容超长被截断 file_id=%s chunk_index=%d 原长=%d 字节",
                file_record.id,
                chunk.index,
                len(chunk.text.encode("utf-8")),
            )

        rows.append(
            {
                "chunk_id": _make_chunk_id(document_id, chunk.index),
                "vector": vec,
                "document_id": document_id,
                "content": content,
                # 权限基线：V1.5 不接用户体系，写 ALL
                "allowed_roles": [settings.rag_default_role],
                "entity_tags": entity_tags,
                "metadata": {
                    "filename": file_record.filename,
                    "mime_type": file_record.mime_type,
                    "chunk_index": chunk.index,
                    "ingested_at": _utc_now_iso(),
                },
                "kb_id": str(kb.id),
            }
        )

    # 分批 upsert（PRD §3.4 TASK-02 batch_size=50）
    for i in range(0, len(rows), MILVUS_BATCH_SIZE):
        batch = rows[i : i + MILVUS_BATCH_SIZE]
        resources.milvus.upsert(collection_name=collection_name, data=batch)

    logger.info(
        "Milvus 写入完成 collection=%s file_id=%s rows=%d",
        collection_name,
        document_id,
        len(rows),
    )


async def _step_ner(chunks: list[Chunk]) -> list[list[dict]]:
    """Step 6: 按 chunk LLM NER；软失败原则 + 单 chunk 硬超时。

    并发跑（Semaphore 限流）；任一 chunk NER 失败 / 超时仅返回 []，不抛错（PRD §6.3）。

    硬超时关键：litellm 默认 timeout=60s 在气象论文场景太宽松——一份 PDF 切 100+
    chunk，任一调用 hang 都会拖死 asyncio.gather。这里再加一层 wait_for(25s) 兜底。

    SKIP_NER=true 时直接跳过（V1.5 联调用，大文档 LLM NER 太慢；S5 后评估是否
    换本地 NER 引擎 / 完全砍掉）。
    """
    settings = get_settings()
    if settings.skip_ner:
        logger.warning(
            "SKIP_NER=true 跳过实体抽取（共 %d chunks），所有 entity_tags 写空",
            len(chunks),
        )
        return [[] for _ in chunks]

    from app.kg.ner import run_ner

    sem = asyncio.Semaphore(NER_CONCURRENCY)
    total = len(chunks)
    completed = 0

    async def _safe_ner(idx: int, text: str) -> list[dict]:
        nonlocal completed
        async with sem:
            try:
                result = await asyncio.wait_for(
                    run_ner(text), timeout=NER_SINGLE_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "NER 超时（已软失败） chunk_idx=%d timeout=%ds",
                    idx,
                    NER_SINGLE_TIMEOUT_SECONDS,
                )
                result = []
            except Exception as e:  # noqa: BLE001
                logger.warning("NER 调用失败（软失败） chunk_idx=%d: %s", idx, e)
                result = []
            completed += 1
            # 进度日志：每 10% 或每 10 chunk 打一次，避免淹没
            if (completed * 10) // total != ((completed - 1) * 10) // total or completed % 10 == 0:
                logger.info(
                    "NER 进度: %d/%d (%.0f%%)",
                    completed,
                    total,
                    completed / total * 100,
                )
            return result

    return await asyncio.gather(
        *[_safe_ner(i, c.text) for i, c in enumerate(chunks)]
    )


async def _step_neo4j_write(
    resources: TaskResources,
    *,
    kb: KnowledgeBase,
    file_record: KbFile,
    chunks: list[Chunk],
    chunk_entities: list[list[dict]],
) -> int:
    """Step 7: 写入 Neo4j Document + Entity + MENTIONED_IN。

    所有节点都带 kb_id 属性，便于 FILE-04 删除时按 kb_id+document_id 精确定位。

    注意：app.kg.writer 的 upsert_* / bulk_* 内部 import 全局 driver；
    这里直接传 resources.neo4j 进自定义事务避免全局依赖。
    返回成功 upsert 的唯一实体数。
    """
    settings = get_settings()
    document_id = str(file_record.id)
    kb_id_str = str(kb.id)

    # ── Document 节点（带 kb_id）─────────────────────────────────
    upsert_doc_cypher = """
    MERGE (d:Document {document_id: $document_id})
    SET d.title = $title,
        d.kb_id = $kb_id,
        d.created_at = coalesce(d.created_at, $created_at)
    RETURN d.document_id AS document_id
    """.strip()

    async with resources.neo4j.session(database=settings.neo4j_database) as sess:
        await sess.run(
            upsert_doc_cypher,
            document_id=document_id,
            title=file_record.filename,
            kb_id=kb_id_str,
            created_at=_utc_now_iso(),
        )

    # ── 实体 + 关系（带 kb_id 标签到 Entity 节点）────────────────
    # 去重 + 收集 + 防御性截断（与 Milvus 写入用同一套 UTF-8 字节长度限制，
    # 保证 Neo4j 与 Milvus 的实体名严格一致 —— 后续 Graph RAG entity_tags
    # 过滤才能跨库匹配）
    entity_rows: list[dict] = []
    link_rows: list[dict] = []
    seen_entities: set[tuple[str, str]] = set()

    for chunk, ents in zip(chunks, chunk_entities):
        chunk_id = _make_chunk_id(document_id, chunk.index)
        for e in ents:
            name = (e.get("name") or "").strip()
            etype = (e.get("type") or "").strip()
            if not name or not etype:
                continue
            # 按 UTF-8 字节截断（与 Milvus entity_tags 同款）
            name = _truncate_utf8(name, _MAX_ENTITY_TAG_BYTES)
            key = (name, etype)
            if key not in seen_entities:
                seen_entities.add(key)
                entity_rows.append(
                    {
                        "name": name,
                        "type": etype,
                        "document_id": document_id,
                        "kb_id": kb_id_str,
                    }
                )
            link_rows.append(
                {
                    "name": name,
                    "type": etype,
                    "document_id": document_id,
                    "chunk_id": chunk_id,
                }
            )

    if not entity_rows:
        return 0

    # 实体 upsert（用本任务的 driver，复刻 app.kg.writer 的 Cypher 但加 kb_id）
    bulk_upsert_cypher = """
    UNWIND $rows AS row
    MERGE (e:Entity {name: row.name, type: row.type})
    ON CREATE SET e.document_ids = [row.document_id],
                  e.kb_id = row.kb_id
    ON MATCH SET e.document_ids =
        CASE
            WHEN row.document_id IN coalesce(e.document_ids, [])
            THEN e.document_ids
            ELSE coalesce(e.document_ids, []) + row.document_id
        END,
        e.kb_id = coalesce(e.kb_id, row.kb_id)
    RETURN count(e) AS n
    """.strip()

    async with resources.neo4j.session(database=settings.neo4j_database) as sess:
        await sess.run(bulk_upsert_cypher, rows=entity_rows)

    # 关系建立（与 app.kg.writer.BULK_LINK_CYPHER 一致）
    bulk_link_cypher = """
    UNWIND $rows AS row
    MATCH (e:Entity {name: row.name, type: row.type})
    MATCH (d:Document {document_id: row.document_id})
    MERGE (e)-[r:MENTIONED_IN {chunk_id: row.chunk_id}]->(d)
    RETURN count(r) AS n
    """.strip()

    async with resources.neo4j.session(database=settings.neo4j_database) as sess:
        await sess.run(bulk_link_cypher, rows=link_rows)

    logger.info(
        "Neo4j 写入完成 file_id=%s entities=%d links=%d",
        document_id,
        len(entity_rows),
        len(link_rows),
    )
    return len(entity_rows)


# ──────────────── async _main ────────────────


async def _main(file_id_str: str, kb_id_str: str) -> dict:
    """七步入库管道；所有异常上抛由 Celery task 体捕获分类。"""
    file_id = uuid.UUID(file_id_str)
    kb_id = uuid.UUID(kb_id_str)

    async with task_resources() as resources:
        # Step 1: 标记 processing
        await _set_progress(
            resources, file_id, progress=0, status=FILE_STATUS_PROCESSING
        )
        file_record, kb = await _load_file_record(resources, file_id)
        if file_record.kb_id != kb_id:
            raise ValueError(
                f"file_id={file_id} 实际 kb_id={file_record.kb_id} 与传入 {kb_id} 不一致"
            )

        # Step 2: 解析
        text = await _step_parse(file_record)
        await _set_progress(resources, file_id, progress=PROGRESS_PARSED)

        # Step 3: 切片
        chunks = _step_split(text, kb)
        if not chunks:
            raise ParseError(
                f"切片结果为空 file_id={file_id} (text_len={len(text)})"
            )
        await _set_progress(resources, file_id, progress=PROGRESS_SPLIT)

        # Step 4: embedding
        vectors = await _step_embed(chunks)
        await _set_progress(resources, file_id, progress=PROGRESS_EMBEDDED)
        logger.info(
            "embedding 完成 file_id=%s vectors=%d，开始 NER", file_id, len(vectors)
        )

        # Step 6: NER（PRD §3.4 顺序：Step 5 Milvus / Step 6 NER；
        # 这里 NER 先跑，把 entity_tags 在 Step 5 一并写入 Milvus，避免重复 upsert）
        chunk_entities = await _step_ner(chunks)
        entity_count_total = sum(len(es) for es in chunk_entities)
        logger.info(
            "NER 完成 file_id=%s total_entities=%d，开始写 Milvus",
            file_id,
            entity_count_total,
        )
        # 注意 progress 仍按 PRD 阶段；NER 完了但还没写库不算到 90
        # 等 Milvus 写完才到 80，NER 实际作用在 80→90 那段

        # Step 5: Milvus 写入（携带 NER 抽出的 entity_tags）
        _step_milvus_write(
            resources,
            kb=kb,
            file_record=file_record,
            chunks=chunks,
            vectors=vectors,
            chunk_entities=chunk_entities,
        )
        await _set_progress(
            resources, file_id, progress=PROGRESS_MILVUS, chunk_count=len(chunks)
        )
        await _bump_kb_chunk_count(resources, kb_id, delta=len(chunks))

        # Step 6 progress 锚点（NER 已完成）
        await _set_progress(
            resources,
            file_id,
            progress=PROGRESS_NER,
            entity_count=entity_count_total,
        )

        # Step 7: Neo4j 写入（注意：NER 软失败时 entity_rows 为空，下面会直接返 0）
        try:
            written_entity_count = await _step_neo4j_write(
                resources,
                kb=kb,
                file_record=file_record,
                chunks=chunks,
                chunk_entities=chunk_entities,
            )
        except Exception as e:  # noqa: BLE001
            # Neo4j 失败不应阻断主链路（与 NER 软失败保持一致原则）
            logger.warning(
                "Neo4j 写入失败（软失败） file_id=%s: %s", file_id, e
            )
            written_entity_count = 0
        await _set_progress(
            resources,
            file_id,
            progress=PROGRESS_NEO4J,
            entity_count=written_entity_count,
        )

        # Step 8: 完成
        completed_at = datetime.now(timezone.utc)
        await _set_progress(
            resources,
            file_id,
            progress=PROGRESS_DONE,
            status=FILE_STATUS_COMPLETED,
            completed_at=completed_at,
        )

        return {
            "file_id": file_id_str,
            "kb_id": kb_id_str,
            "chunk_count": len(chunks),
            "entity_count": written_entity_count,
            "status": FILE_STATUS_COMPLETED,
        }


# ──────────────── 异常分类（TASK-03） ────────────────


def _classify_retryable(exc: BaseException) -> bool:
    """判断异常是否值得重试。

    可重试：MilvusException / 网络抖动 / RedisConnectionError 等
    不可重试：参数错（ValueError）、文件 / 解析错（ParseError）、维度不匹配
    """
    # 不可重试白名单
    if isinstance(exc, (ValueError, ParseError, TypeError, FileNotFoundError)):
        return False
    # Milvus / Redis 异常名做字符串判（避免硬 import）
    name = type(exc).__name__
    if name in (
        "MilvusException",
        "RpcError",
        "ConnectionError",
        "TimeoutError",
        "RedisConnectionError",
    ):
        return True
    # 其他默认不重试（worker 主动 retry 比无脑重试稳）
    return False


# ──────────────── Celery 任务入口 ────────────────


@celery_app.task(
    name="app.tasks.ingest_task.parse_and_ingest_task",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def parse_and_ingest_task(self, file_id: str, kb_id: str) -> dict:
    """文件解析入库任务入口（同步 def，内部 asyncio.run）。

    args:
        file_id: str(UUID)
        kb_id:   str(UUID)
    """
    logger.info(
        "ingest 任务开始 file_id=%s kb_id=%s task_id=%s attempt=%d",
        file_id,
        kb_id,
        self.request.id,
        self.request.retries + 1,
    )
    try:
        return asyncio.run(_main(file_id, kb_id))
    except Exception as exc:  # noqa: BLE001
        retryable = _classify_retryable(exc)
        tb = traceback.format_exc(limit=20)
        logger.error(
            "ingest 任务失败 file_id=%s retryable=%s err=%s",
            file_id,
            retryable,
            exc,
        )

        # 把错误信息落库（让前端 GET /files/{id} 能看到）
        try:
            asyncio.run(
                _mark_failed_safe(file_id, error_message=f"{type(exc).__name__}: {exc}")
            )
        except Exception as inner:  # noqa: BLE001
            logger.error("ingest 任务失败时回写 status=failed 失败: %s", inner)

        if retryable and self.request.retries < (self.max_retries or 0):
            # 指数退避：30s → 60s → 120s
            countdown = 30 * (2**self.request.retries)
            logger.info(
                "ingest 任务进入重试 file_id=%s countdown=%ds attempt=%d/%d",
                file_id,
                countdown,
                self.request.retries + 1,
                self.max_retries,
            )
            raise self.retry(exc=exc, countdown=countdown)

        # 不可重试或重试次数用完：吃掉异常返回错误信息（避免 Celery 把堆栈传给 broker）
        return {
            "file_id": file_id,
            "kb_id": kb_id,
            "status": FILE_STATUS_FAILED,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": tb,
        }


async def _mark_failed_safe(file_id: str, *, error_message: str) -> None:
    """异常路径里调；独立 task_resources，避免主管道异常时连不上 PG 也写不进失败状态。"""
    async with task_resources() as resources:
        async with resources.db() as session:
            await session.execute(
                update(KbFile)
                .where(KbFile.id == uuid.UUID(file_id))
                .values(
                    status=FILE_STATUS_FAILED,
                    error_message=error_message[:2000],
                )
            )
            await session.commit()


__all__ = ["parse_and_ingest_task"]
