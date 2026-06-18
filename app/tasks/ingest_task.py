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
import dataclasses
import hashlib
import logging
import traceback
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from app.core.async_utils import gather_with_timeout
from app.core.config import get_settings
from app.ingest.doc_metadata import DocMetadata, extract_doc_metadata
from app.ingest.dual_layer import CoarseChunk, generate_coarse_chunks
from app.ingest.parser import ParseError, StructuredBlock, parse_document_structured
from app.ingest.structured_splitter import StructuredChunk, split_structured_blocks
from app.ingest.table_description import TableDescription, generate_table_descriptions
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


async def _mark_neo4j_degraded(
    resources: TaskResources,
    file_id: uuid.UUID,
    error: Exception,
) -> None:
    """把 Neo4j 软失败写入文件元数据，区分“无实体”和“图谱写入失败”。"""
    warning = {
        "neo4j_failed": True,
        "neo4j_error_type": type(error).__name__,
        "neo4j_error": str(error)[:500],
        "neo4j_failed_at": _utc_now_iso(),
    }
    async with resources.db() as session:
        row = (
            await session.execute(select(KbFile).where(KbFile.id == file_id))
        ).scalar_one_or_none()
        if row is None:
            return
        doc_metadata = dict(row.doc_metadata or {})
        ingest_warnings = dict(doc_metadata.get("_ingest_warnings") or {})
        ingest_warnings.update(warning)
        doc_metadata["_ingest_warnings"] = ingest_warnings
        await session.execute(
            update(KbFile)
            .where(KbFile.id == file_id)
            .values(doc_metadata=doc_metadata)
        )
        await session.commit()


async def _cleanup_milvus_chunks_for_file(
    resources: TaskResources,
    kb_id: uuid.UUID,
    file_id: uuid.UUID,
) -> None:
    """失败补偿：按 document_id 清理任务级 Milvus client 中的残留切片。"""
    collection_name = build_kb_collection_name(kb_id)
    try:
        if not resources.milvus.has_collection(collection_name):
            return
        await asyncio.to_thread(
            resources.milvus.delete,
            collection_name=collection_name,
            filter=f'document_id == "{file_id}"',
        )
        logger.info("失败补偿已清理 Milvus collection=%s file_id=%s", collection_name, file_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "失败补偿清理 Milvus 失败 collection=%s file_id=%s err=%s",
            collection_name,
            file_id,
            e,
        )


async def _cleanup_neo4j_document_for_file(
    resources: TaskResources,
    kb_id: uuid.UUID,
    file_id: uuid.UUID,
) -> None:
    """失败补偿：删除任务级 Neo4j driver 中该文件的 Document 节点。"""
    settings = get_settings()
    delete_cypher = """
    MATCH (d:Document {document_id: $document_id, kb_id: $kb_id})
    DETACH DELETE d
    """.strip()
    try:
        async with resources.neo4j.session(database=settings.neo4j_database) as sess:
            await sess.run(delete_cypher, document_id=str(file_id), kb_id=str(kb_id))
        logger.info("失败补偿已清理 Neo4j Document kb_id=%s file_id=%s", kb_id, file_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("失败补偿清理 Neo4j 失败 kb_id=%s file_id=%s err=%s", kb_id, file_id, e)


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


async def _step_table_description(
    fine_chunks: list[StructuredChunk],
    *,
    document_id: str,
) -> list[StructuredChunk]:
    """Step 4: 表格描述生成（IDP-03）。

    对每张 ``block_type=="table"`` 的 chunk 生成自然语言描述，作为额外
    ``StructuredChunk`` 返回（block_type="table_description"，
    parent_chunk_id 指向原表格 chunk 的 INT64 chunk_id 字符串）。

    新 chunk 的 ``index`` 从 ``len(fine_chunks)`` 起递增，与细粒度 chunk
    的 chunk_id 不冲突，幂等 upsert 仍稳定。
    """
    descriptions = await generate_table_descriptions(fine_chunks)
    if not descriptions:
        return []

    td_chunks: list[StructuredChunk] = []
    base_index = len(fine_chunks)
    for offset, desc in enumerate(descriptions):
        parent = fine_chunks[desc.parent_index]
        parent_chunk_id_int = _make_chunk_id_int(document_id, parent.index)
        td_chunks.append(
            StructuredChunk(
                chunk_id=str(uuid.uuid4().hex),
                index=base_index + offset,
                content=desc.description,
                heading_path=list(parent.heading_path),
                block_type="table_description",
                page_number=parent.page_number,
                position_index=parent.position_index,
                parent_chunk_id=str(parent_chunk_id_int),
                is_summary=False,
            )
        )
    return td_chunks


async def _step_dual_layer_index(
    fine_chunks: list[StructuredChunk],
    *,
    td_chunk_count: int,
    document_id: str,
) -> tuple[list[StructuredChunk], list[StructuredChunk]]:
    """Step 5: 双层索引（IDP-04）。

    1. 调 :func:`generate_coarse_chunks` 按父级 heading_path 聚合 + LLM 摘要
    2. 转换 ``CoarseChunk`` → ``StructuredChunk(is_summary=True)``，index 从
       ``len(fine_chunks) + td_chunk_count`` 起，确保三类 chunk_id 不冲突
    3. **回填** fine_chunks 的 ``parent_chunk_id``：把每个粗 chunk 的 INT64
       chunk_id 字符串写到它聚合的所有 fine chunk 的 parent_chunk_id 字段

    Returns:
        ``(updated_fine_chunks, coarse_chunks)``：更新后的 fine_chunks（其中
        被聚合的 chunk 已回填 parent_chunk_id）和新生成的粗 chunk 列表。
        ``IDP_DUAL_INDEX_ENABLE=False`` 时返 ``(fine_chunks, [])`` 不做任何修改。
    """
    coarse_intermediates = await generate_coarse_chunks(fine_chunks)
    if not coarse_intermediates:
        return fine_chunks, []

    base_index = len(fine_chunks) + td_chunk_count
    coarse_chunks: list[StructuredChunk] = []

    # 收集需要回填 parent_chunk_id 的 fine chunk 下标 → 父 chunk_id 字符串
    parent_id_overrides: dict[int, str] = {}

    for offset, ci in enumerate(coarse_intermediates):
        coarse_idx = base_index + offset
        coarse_chunk_id_int = _make_chunk_id_int(document_id, coarse_idx)
        coarse_chunks.append(
            StructuredChunk(
                chunk_id=str(uuid.uuid4().hex),
                index=coarse_idx,
                content=ci.summary_text,
                heading_path=list(ci.heading_path),
                block_type="paragraph",
                page_number=ci.page_number,
                # 粗 chunk 的 position_index 取首个被聚合 fine chunk 的 position
                position_index=fine_chunks[ci.parent_indices[0]].position_index,
                parent_chunk_id=None,  # 粗 chunk 自身是 parent
                is_summary=True,
            )
        )
        # 回填子 chunk 的 parent_chunk_id
        coarse_id_str = str(coarse_chunk_id_int)
        for fine_idx in ci.parent_indices:
            parent_id_overrides[fine_idx] = coarse_id_str

    # frozen dataclass 用 dataclasses.replace 重建
    updated_fine: list[StructuredChunk] = []
    for i, c in enumerate(fine_chunks):
        if i in parent_id_overrides:
            updated_fine.append(
                dataclasses.replace(c, parent_chunk_id=parent_id_overrides[i])
            )
        else:
            updated_fine.append(c)

    return updated_fine, coarse_chunks


async def _step_doc_metadata(
    resources: TaskResources,
    *,
    file_record: KbFile,
    blocks: list[StructuredBlock],
) -> DocMetadata | None:
    """Step 6: 文档元数据提取（IDP-05）。

    成功时把 ``doc_metadata`` JSONB 与 ``summary_brief`` 写入 ``kb_files`` 表；
    失败软降级（保留两个字段为 None）。
    """
    meta = await extract_doc_metadata(blocks)
    if meta is None:
        logger.warning("IDP-05 文档元数据为空（已软失败），跳过 PG 写入 file_id=%s", file_record.id)
        return None

    async with resources.db() as session:
        await session.execute(
            update(KbFile)
            .where(KbFile.id == file_record.id)
            .values(
                doc_metadata=meta.to_dict(),
                summary_brief=meta.summary_brief,
            )
        )
        await session.commit()

    logger.info(
        "IDP-05 文档元数据写入完成 file_id=%s doc_type=%s topics=%d",
        file_record.id, meta.doc_type, len(meta.key_topics),
    )
    return meta


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
        create_v2_kb_collection(kb.id, dim=kb.embedding_dim, client=resources.milvus)

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

    try:
        return await gather_with_timeout(
            [_safe_ner(i, c.content) for i, c in enumerate(chunks)],
            timeout_s=max(NER_SINGLE_TIMEOUT_SECONDS + 5, len(chunks) * NER_SINGLE_TIMEOUT_SECONDS / NER_CONCURRENCY + 5),
            label="ingest_ner",
        )
    except asyncio.TimeoutError:
        logger.warning("NER 整组超时（软失败），返回空实体 chunks=%d", len(chunks))
        return [[] for _ in chunks]


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
        fine_chunks = _step_split_structured(blocks, kb)
        await _set_progress(resources, file_id, progress=PROGRESS_SPLIT)

        document_id = str(file_record.id)

        # Step 4: 表格描述生成（IDP-03）—— 软失败：单张表 LLM 失败不影响其他
        td_chunks = await _step_table_description(fine_chunks, document_id=document_id)
        await _set_progress(resources, file_id, progress=PROGRESS_TABLE_DESC)

        # Step 5: 双层索引（IDP-04）—— 同时回填 fine_chunks 的 parent_chunk_id
        fine_chunks, coarse_chunks = await _step_dual_layer_index(
            fine_chunks,
            td_chunk_count=len(td_chunks),
            document_id=document_id,
        )
        await _set_progress(resources, file_id, progress=PROGRESS_SUMMARY)

        # Step 6: 文档元数据提取（IDP-05）—— 软失败：失败时 doc_metadata 留空
        await _step_doc_metadata(resources, file_record=file_record, blocks=blocks)
        await _set_progress(resources, file_id, progress=PROGRESS_DOC_META)

        # 合并三类 chunk 后续 embedding / Milvus / NER 步骤共用此列表
        # 顺序：fine（含已回填 parent_chunk_id）→ table_description → coarse
        chunks = fine_chunks + td_chunks + coarse_chunks

        # Step 7: 批量向量嵌入（自然包含三类 chunk）
        vectors = await _step_embed(chunks)
        await _set_progress(resources, file_id, progress=PROGRESS_EMBEDDED)
        logger.info("embedding 完成 file_id=%s vectors=%d", file_id, len(vectors))

        # Step 9: NER —— 仅对 fine_chunks 跑（粗粒度摘要 + 表格描述都是合成文本，
        # 不应抽出新实体）。给 td/coarse 补空 entities 列表，对齐 zip 长度。
        chunk_entities_fine = await _step_ner(fine_chunks)
        entity_count_total = sum(len(es) for es in chunk_entities_fine)
        chunk_entities = (
            chunk_entities_fine + [[] for _ in td_chunks] + [[] for _ in coarse_chunks]
        )

        # Step 8: Milvus V2 写入（携带 entity_tags + 结构元数据 + parent_chunk_id + is_summary）
        await asyncio.to_thread(
            _step_milvus_write_v2,
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
            await _mark_neo4j_degraded(resources, file_id, e)
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
            "fine_chunk_count": len(fine_chunks),
            "table_description_count": len(td_chunks),
            "coarse_chunk_count": len(coarse_chunks),
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
                _mark_failed_safe(
                    file_id,
                    kb_id=kb_id,
                    error_message=f"{type(exc).__name__}: {exc}",
                )
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


async def _mark_failed_safe(file_id: str, *, kb_id: str, error_message: str) -> None:
    """异常路径里调；独立 task_resources，并尽力清理跨存储残留。"""
    file_uuid = uuid.UUID(file_id)
    kb_uuid = uuid.UUID(kb_id)
    async with task_resources() as resources:
        await _cleanup_milvus_chunks_for_file(resources, kb_uuid, file_uuid)
        await _cleanup_neo4j_document_for_file(resources, kb_uuid, file_uuid)
        async with resources.db() as session:
            await session.execute(
                update(KbFile)
                .where(KbFile.id == file_uuid)
                .values(
                    status=FILE_STATUS_FAILED,
                    error_message=error_message[:2000],
                )
            )
            await session.commit()


__all__ = ["parse_and_ingest_task"]
