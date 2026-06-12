"""文件入库 Celery 任务（V2.0 IDP-06 十一步管道重构）。

V1.5 七步管道已归档为 ingest_task_v1.py，V2.0 全面替换。

【架构约定 - 同 V1.5】
- Celery @task 用同步 def；核心 async def _main；体内只调一次 asyncio.run
- 所有外部连接（PG / Milvus / Neo4j）由 task_resources() 在 _main 入口现建、退出时 dispose
- 不依赖 app.main 全局单例（worker 进程无 lifespan）

【V2.0 十一步管道（IDP-06）】
    Step  1: status=processing, progress=0                 任务入口
    Step  2: 结构感知解析（IDP-01）                        progress=15
    Step  3: 结构感知切片（IDP-02）                        progress=25
    Step  4: 表格描述生成（IDP-03，T7 接通；当前 noop）    progress=30
    Step  5: 段落摘要生成（IDP-04，T7 接通；当前 noop）    progress=40
    Step  6: 文档元数据提取（IDP-05，T7 接通；当前 noop）   progress=45
    Step  7: 批量向量嵌入                                  progress=65
    Step  8: 写入 Milvus（V2 Schema）                      progress=80
    Step  9: NER 实体抽取 → 写入 Neo4j                     progress=92
    Step 10: 写入 BM25 稀疏向量（T2 接通；当前 noop）      progress=97
    Step 11: status=completed, progress=100

【PRD §3.4 TASK-03 重试策略 - 同 V1.5】
- autoretry_for=(MilvusException, RedisConnectionError)
- max_retries=3, 指数退避 30s → 60s → 120s
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
from app.ingest.parser import ParseError, StructuredBlock, parse_document_structured
from app.ingest.structured_splitter import StructuredChunk, split_structured_blocks
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
from app.rag.milvus_client import create_v2_kb_collection
from app.rag.naming import build_kb_collection_name
from app.tasks._resources import TaskResources, task_resources
from app.tasks.celery_app import celery_app

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ──────────────── 常量 ────────────────


# V2.0 十一步管道的 progress 锚点
PROGRESS_START = 0
PROGRESS_PARSED = 15
PROGRESS_SPLIT = 25
PROGRESS_TABLE_DESC = 30  # T7 接通
PROGRESS_SUMMARY = 40  # T7 接通
PROGRESS_DOC_META = 45  # T7 接通
PROGRESS_EMBEDDED = 65
PROGRESS_MILVUS = 80
PROGRESS_NER = 92
PROGRESS_BM25 = 97  # T2 接通
PROGRESS_DONE = 100

# Embedding 批大小
EMBEDDING_BATCH_SIZE = 32

# Milvus 批写入大小
MILVUS_BATCH_SIZE = 50

# NER 并发限制
NER_CONCURRENCY = 8
NER_SINGLE_TIMEOUT_SECONDS = 25

# Milvus 字段长度上限（防御性截断，同 V1.5）
_MAX_ENTITY_TAG_BYTES = 64
_MAX_ENTITY_TAGS_PER_CHUNK = 50
_MAX_CONTENT_BYTES = 65535
_MAX_HEADING_PATH_LEN = 256  # UTF-8 字节
_MAX_BLOCK_TYPE_LEN = 32
_MAX_PARENT_CHUNK_ID_LEN = 64


def _truncate_utf8(s: str, max_bytes: int) -> str:
    """按 UTF-8 字节数安全截断；不切断多字节字符。"""
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


# ──────────────── 工具函数 ────────────────


def _make_chunk_id_int(document_id: str, chunk_index: int) -> int:
    """生成稳定 INT64 chunk_id（同 V1.5 策略，upsert 幂等）。"""
    key = f"{document_id}::{chunk_index}".encode("utf-8")
    h = hashlib.sha256(key).digest()
    raw = int.from_bytes(h[:8], byteorder="big", signed=False)
    return raw & 0x7FFF_FFF_FFFF_FFFF


def _utc_now_iso() -> str:
    """ISO 8601 UTC 时间戳。"""
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
    """更新 kb_files 行的进度字段。"""
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
        values["error_message"] = error_message[:2000]

    async with resources.db() as session:
        await session.execute(
            update(KbFile).where(KbFile.id == file_id).values(**values)
        )
        await session.commit()


async def _load_file_record(
    resources: TaskResources, file_id: uuid.UUID
) -> tuple[KbFile, KnowledgeBase]:
    """加载文件 + 关联 KB。"""
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

        session.expunge(f)
        session.expunge(kb)
    return f, kb


async def _bump_kb_chunk_count(
    resources: TaskResources, kb_id: uuid.UUID, delta: int
) -> None:
    """原子地把 KB.chunk_count += delta。"""
    if delta == 0:
        return
    async with resources.db() as session:
        await session.execute(
            update(KnowledgeBase)
            .where(KnowledgeBase.id == kb_id)
            .values(chunk_count=KnowledgeBase.chunk_count + delta)
        )
        await session.commit()


# ──────────────── V2.0 十一步管道 ────────────────


async def _step_parse_structured(file_record: KbFile) -> list[StructuredBlock]:
    """Step 2: 结构感知解析（IDP-01）。"""
    blocks = parse_document_structured(
        file_record.file_path, filename=file_record.filename
    )
    if not blocks:
        raise ParseError(
            f"文件解析后内容为空 file_id={file_record.id} path={file_record.file_path}"
        )
    return blocks


def _step_split_structured(
    blocks: list[StructuredBlock], kb: KnowledgeBase
) -> list[StructuredChunk]:
    """Step 3: 结构感知切片（IDP-02）。"""
    chunks = split_structured_blocks(
        blocks,
        chunk_size=kb.chunk_size,
        chunk_overlap=kb.chunk_overlap,
    )
    if not chunks:
        raise ParseError("结构感知切片结果为空")
    return chunks


def _step_table_description_noop(chunks: list[StructuredChunk]) -> None:
    """Step 4: 表格描述生成（IDP-03，T7 接通；当前 noop）。

    T7 阶段会为 table 类型 chunk 生成自然语言描述，
    增强表格内容的可检索性。当前阶段跳过。
    """
    pass


def _step_summary_noop(chunks: list[StructuredChunk]) -> None:
    """Step 5: 段落摘要生成（IDP-04，T7 接通；当前 noop）。

    T7 阶段会为每个 chunk 生成摘要，写入 parent_chunk_id + is_summary 字段，
    实现双层索引。当前阶段跳过。
    """
    pass


def _step_doc_metadata_noop(file_record: KbFile, blocks: list[StructuredBlock]) -> None:
    """Step 6: 文档元数据提取（IDP-05，T7 接通；当前 noop）。

    T7 阶段会从文档中提取标题/作者/日期等元数据，
    写入 KbFile.doc_metadata 和 KbFile.summary_brief。当前阶段跳过。
    """
    pass


async def _step_embed(chunks: list[StructuredChunk]) -> list[list[float]]:
    """Step 7: 批量向量嵌入。"""
    vectors: list[list[float]] = []
    for i in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
        batch = chunks[i : i + EMBEDDING_BATCH_SIZE]
        texts = [c.content for c in batch]
        batch_vecs = await aembed_texts(texts)
        vectors.extend(batch_vecs)
    return vectors


def _step_milvus_write_v2(
    resources: TaskResources,
    *,
    kb: KnowledgeBase,
    file_record: KbFile,
    chunks: list[StructuredChunk],
    vectors: list[list[float]],
    chunk_entities: list[list[dict]] | None = None,
) -> None:
    """Step 8: 写入 Milvus V2 Schema（含 heading_path / block_type / sparse_vector 等新字段）。

    V2.0 与 V1.5 的差异：
    - 使用 V2 Schema（15 字段）
    - 写入 heading_path / block_type / page_number / position_index / parent_chunk_id / is_summary
    - sparse_vector 暂写空（T2 阶段才填实）
    """
    settings = get_settings()
    collection_name = build_kb_collection_name(kb.id)
    document_id = str(file_record.id)

    # 自愈：确保 V2 collection 存在
    if not resources.milvus.has_collection(collection_name):
        logger.warning(
            "V2 Collection %s 不存在，尝试自愈创建（kb_id=%s dim=%d）",
            collection_name,
            kb.id,
            kb.embedding_dim,
        )
        import app.rag.milvus_client as mod

        prev_client = mod._client
        mod._client = resources.milvus
        try:
            create_v2_kb_collection(kb.id, dim=kb.embedding_dim)
        finally:
            mod._client = prev_client

    rows: list[dict] = []
    for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
        # entity_tags 处理（同 V1.5 逻辑）
        entity_tags: list[str] = []
        if chunk_entities is not None and i < len(chunk_entities):
            seen: set[str] = set()
            for e in chunk_entities[i]:
                name = (e.get("name") or "").strip()
                if not name:
                    continue
                truncated = _truncate_utf8(name, _MAX_ENTITY_TAG_BYTES)
                if truncated in seen:
                    continue
                seen.add(truncated)
                entity_tags.append(truncated)
                if len(entity_tags) >= _MAX_ENTITY_TAGS_PER_CHUNK:
                    break

        # content 截断
        content = _truncate_utf8(chunk.content, _MAX_CONTENT_BYTES)

        # heading_path 截断（每个元素按 UTF-8 字节截断）
        heading_path = [
            _truncate_utf8(h, _MAX_HEADING_PATH_LEN) for h in chunk.heading_path
        ]

        rows.append(
            {
                "chunk_id": _make_chunk_id_int(document_id, chunk.index),
                "vector": vec,
                "document_id": document_id,
                "content": content,
                "allowed_roles": [settings.rag_default_role],
                "entity_tags": entity_tags,
                "metadata": {
                    "filename": file_record.filename,
                    "mime_type": file_record.mime_type,
                    "chunk_index": chunk.index,
                    "ingested_at": _utc_now_iso(),
                },
                "kb_id": str(kb.id),
                # V2.0 新增字段
                "heading_path": heading_path,
                "block_type": chunk.block_type[:_MAX_BLOCK_TYPE_LEN],
                "page_number": chunk.page_number,
                "position_index": chunk.position_index,
                "parent_chunk_id": chunk.parent_chunk_id,
                "is_summary": chunk.is_summary,
                # sparse_vector 不需要手动填写！
                # V2 Schema 的 BM25 Function 会从 content 字段自动生成稀疏向量。
                # 插入数据时只要包含 content 字段，Milvus 自动计算 BM25 稀疏向量。
            }
        )

    # 分批 upsert
    for i in range(0, len(rows), MILVUS_BATCH_SIZE):
        batch = rows[i : i + MILVUS_BATCH_SIZE]
        resources.milvus.upsert(collection_name=collection_name, data=batch)

    logger.info(
        "Milvus V2 写入完成 collection=%s file_id=%s rows=%d",
        collection_name,
        document_id,
        len(rows),
    )


async def _step_ner(chunks: list[StructuredChunk]) -> list[list[dict]]:
    """Step 9: NER 实体抽取 → 写入 Neo4j（同 V1.5 逻辑，软失败）。"""
    settings = get_settings()
    if settings.skip_ner:
        logger.warning("SKIP_NER=true 跳过实体抽取（共 %d chunks）", len(chunks))
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
                logger.warning("NER 超时（软失败） chunk_idx=%d", idx)
                result = []
            except Exception as e:  # noqa: BLE001
                logger.warning("NER 调用失败（软失败） chunk_idx=%d: %s", idx, e)
                result = []
            completed += 1
            if (completed * 10) // total != ((completed - 1) * 10) // total or completed % 10 == 0:
                logger.info("NER 进度: %d/%d (%.0f%%)", completed, total, completed / total * 100)
            return result

    return await asyncio.gather(
        *[_safe_ner(i, c.content) for i, c in enumerate(chunks)]
    )


def _step_bm25_auto() -> None:
    """Step 10: BM25 稀疏向量确认（V2 Schema BM25 Function 已自动生成）。

    V2 Schema 的 BM25 Function 在 Step 8 插入数据时已自动从 content 字段
    生成 sparse_vector，此步骤仅为日志确认 + progress 锚点。
    不需要额外操作。
    """
    logger.info("BM25 稀疏向量已由 Milvus BM25 Function 自动生成（Step 8 插入时完成）")


# ──────────────── async _main ────────────────


async def _main(file_id_str: str, kb_id_str: str) -> dict:
    """V2.0 十步入库管道。"""
    file_id = uuid.UUID(file_id_str)
    kb_id = uuid.UUID(kb_id_str)

    async with task_resources() as resources:
        # Step 1: 标记 processing
        await _set_progress(
            resources, file_id, progress=PROGRESS_START, status=FILE_STATUS_PROCESSING
        )
        file_record, kb = await _load_file_record(resources, file_id)
        if file_record.kb_id != kb_id:
            raise ValueError(
                f"file_id={file_id} 实际 kb_id={file_record.kb_id} 与传入 {kb_id} 不一致"
            )

        # Step 2: 结构感知解析（IDP-01）
        blocks = await _step_parse_structured(file_record)
        await _set_progress(resources, file_id, progress=PROGRESS_PARSED)

        # Step 3: 结构感知切片（IDP-02）
        chunks = _step_split_structured(blocks, kb)
        await _set_progress(resources, file_id, progress=PROGRESS_SPLIT)

        # Step 4: 表格描述生成（IDP-03，noop）
        _step_table_description_noop(chunks)
        await _set_progress(resources, file_id, progress=PROGRESS_TABLE_DESC)

        # Step 5: 段落摘要生成（IDP-04，noop）
        _step_summary_noop(chunks)
        await _set_progress(resources, file_id, progress=PROGRESS_SUMMARY)

        # Step 6: 文档元数据提取（IDP-05，noop）
        _step_doc_metadata_noop(file_record, blocks)
        await _set_progress(resources, file_id, progress=PROGRESS_DOC_META)

        # Step 7: 批量向量嵌入
        vectors = await _step_embed(chunks)
        await _set_progress(resources, file_id, progress=PROGRESS_EMBEDDED)
        logger.info("embedding 完成 file_id=%s vectors=%d", file_id, len(vectors))

        # Step 9: NER（先跑，entity_tags 在 Step 8 一并写入 Milvus）
        chunk_entities = await _step_ner(chunks)
        entity_count_total = sum(len(es) for es in chunk_entities)

        # Step 8: Milvus V2 写入（携带 entity_tags + 结构元数据）
        _step_milvus_write_v2(
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

        # Step 9 progress 锚点
        await _set_progress(
            resources, file_id, progress=PROGRESS_NER, entity_count=entity_count_total
        )

        # Step 9b: Neo4j 写入（软失败）
        try:
            written_entity_count = await _step_neo4j_write(
                resources,
                kb=kb,
                file_record=file_record,
                chunks=chunks,
                chunk_entities=chunk_entities,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Neo4j 写入失败（软失败） file_id=%s: %s", file_id, e)
            written_entity_count = 0

        # Step 10: BM25 稀疏向量确认（V2 Schema BM25 Function 已在 Step 8 自动生成）
        _step_bm25_auto()
        await _set_progress(resources, file_id, progress=PROGRESS_BM25)

        # Step 11: 完成
        completed_at = datetime.now(timezone.utc)
        await _set_progress(
            resources,
            file_id,
            progress=PROGRESS_DONE,
            status=FILE_STATUS_COMPLETED,
            completed_at=completed_at,
            entity_count=written_entity_count,
        )

        return {
            "file_id": file_id_str,
            "kb_id": kb_id_str,
            "chunk_count": len(chunks),
            "entity_count": written_entity_count,
            "block_types": list({c.block_type for c in chunks}),
            "status": FILE_STATUS_COMPLETED,
        }


async def _step_neo4j_write(
    resources: TaskResources,
    *,
    kb: KnowledgeBase,
    file_record: KbFile,
    chunks: list[StructuredChunk],
    chunk_entities: list[list[dict]],
) -> int:
    """Neo4j 写入（同 V1.5 逻辑，适配 StructuredChunk）。"""
    settings = get_settings()
    document_id = str(file_record.id)
    kb_id_str = str(kb.id)

    # Document 节点
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

    # 实体 + 关系
    entity_rows: list[dict] = []
    link_rows: list[dict] = []
    seen_entities: set[tuple[str, str]] = set()

    for chunk, ents in zip(chunks, chunk_entities):
        chunk_id = _make_chunk_id_int(document_id, chunk.index)
        for e in ents:
            name = (e.get("name") or "").strip()
            etype = (e.get("type") or "").strip()
            if not name or not etype:
                continue
            name = _truncate_utf8(name, _MAX_ENTITY_TAG_BYTES)
            key = (name, etype)
            if key not in seen_entities:
                seen_entities.add(key)
                entity_rows.append(
                    {"name": name, "type": etype, "document_id": document_id, "kb_id": kb_id_str}
                )
            link_rows.append(
                {"name": name, "type": etype, "document_id": document_id, "chunk_id": chunk_id}
            )

    if not entity_rows:
        return 0

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


# ──────────────── 异常分类（同 V1.5）────────────────


def _classify_retryable(exc: BaseException) -> bool:
    """判断异常是否值得重试。"""
    if isinstance(exc, (ValueError, ParseError, TypeError, FileNotFoundError)):
        return False
    name = type(exc).__name__
    if name in (
        "MilvusException",
        "RpcError",
        "ConnectionError",
        "TimeoutError",
        "RedisConnectionError",
    ):
        return True
    return False


# ──────────────── Celery 任务入口 ────────────────


@celery_app.task(
    name="app.tasks.ingest_task.parse_and_ingest_task",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def parse_and_ingest_task(self, file_id: str, kb_id: str) -> dict:
    """文件解析入库任务入口（V2.0 十一步管道）。"""
    logger.info(
        "ingest 任务开始(V2) file_id=%s kb_id=%s task_id=%s attempt=%d",
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

        try:
            asyncio.run(
                _mark_failed_safe(file_id, error_message=f"{type(exc).__name__}: {exc}")
            )
        except Exception as inner:  # noqa: BLE001
            logger.error("ingest 任务失败时回写 status=failed 失败: %s", inner)

        if retryable and self.request.retries < (self.max_retries or 0):
            countdown = 30 * (2**self.request.retries)
            logger.info(
                "ingest 任务进入重试 file_id=%s countdown=%ds",
                file_id,
                countdown,
            )
            raise self.retry(exc=exc, countdown=countdown)

        return {
            "file_id": file_id,
            "kb_id": kb_id,
            "status": FILE_STATUS_FAILED,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": tb,
        }


async def _mark_failed_safe(file_id: str, *, error_message: str) -> None:
    """异常路径里调；独立 task_resources。"""
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
