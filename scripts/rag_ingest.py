"""RAG 入库脚本：把 data/seed/*.txt 切片、NER、Embedding、写入 Milvus + Neo4j。

用途：
  - 验证 Milvus 写入链路（RAG-01）
  - 验证 NER + Neo4j 写入链路（KG-02 / KG-05）
  - 给后续 smoke 脚本和 Agent 端到端测试准备真实数据

运行方式：
  conda activate geo_agent
  cd TyAgent
  python scripts/rag_ingest.py

前置条件：
  - 本地 Milvus 已启动且可访问（默认 http://localhost:19530）
  - 本地 Neo4j 已启动且可访问（默认 bolt://localhost:7687）
  - .env 中已配置 EMBEDDING_* / LITELLM_* / NEO4J_*
  - data/seed/ 下有至少一个 .txt 文件

注意：
  - chunk_id 用 hash(document_id + chunk_index) 取低 63 位，重跑会得到相同 ID
    走 upsert 路径，不会重复积累垃圾数据。
  - Neo4j 全部走 MERGE 语义，重跑同样幂等。
  - NER 失败软降级为空列表，不阻断主链路。
"""

import asyncio
import hashlib
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.kg import (
    bulk_link_entities_to_chunk,
    bulk_upsert_entities,
    close_neo4j,
    init_neo4j,
    run_ner,
    upsert_document,
)
from app.rag import aembed_texts, close_milvus, init_milvus

logger = logging.getLogger(__name__)


# ──────────────────── 切片 ────────────────────

# 单 chunk 最大字符数。VARCHAR(content) 上限是 65535，留充足余量，
# 也避免 embedding API 单次输入 token 超限。
_MAX_CHUNK_LEN = 800

# NER 并发上限：太高容易触发 LLM 厂商限流
_NER_CONCURRENCY = 5


def _split_paragraphs(text: str) -> list[str]:
    """按连续空行切段落，去掉空白段。"""
    raw_paras = [p.strip() for p in text.split("\n\n")]
    return [p for p in raw_paras if p]


def _hard_chunk(text: str, max_len: int = _MAX_CHUNK_LEN) -> list[str]:
    """段落过长时硬切：按 max_len 截断。简单可靠，不做语义切。"""
    if len(text) <= max_len:
        return [text]
    chunks = []
    for i in range(0, len(text), max_len):
        chunks.append(text[i : i + max_len])
    return chunks


def split_document(text: str) -> list[str]:
    """文档 → chunk 列表。段落优先，过长段落硬切。"""
    chunks: list[str] = []
    for para in _split_paragraphs(text):
        chunks.extend(_hard_chunk(para))
    return chunks


# ──────────────────── chunk_id 生成 ────────────────────


def make_chunk_id(document_id: str, chunk_index: int) -> int:
    """hash(document_id + chunk_index) 取低 63 位作为 INT64 主键。

    重跑同一份数据会得到相同 ID，走 upsert 路径，不会重复积累垃圾。
    """
    key = f"{document_id}::{chunk_index}".encode("utf-8")
    h = hashlib.sha256(key).digest()
    # 取前 8 字节转 INT64，再 mask 掉符号位保证非负
    raw = int.from_bytes(h[:8], byteorder="big", signed=False)
    return raw & 0x7FFFFFFFFFFFFFFF  # 低 63 位


# ──────────────────── NER 并发执行 ────────────────────


async def _ner_with_semaphore(sem: asyncio.Semaphore, chunk_text: str) -> list[dict]:
    """带信号量的 NER 调用，控制全局并发。"""
    async with sem:
        return await run_ner(chunk_text)


async def batch_ner(chunks: list[str]) -> list[list[dict]]:
    """对一批 chunk 并发跑 NER，返回与 chunks 等长的实体列表。

    NER 失败的 chunk 在结果中是 []（软降级），不会抛错。
    """
    sem = asyncio.Semaphore(_NER_CONCURRENCY)
    tasks = [_ner_with_semaphore(sem, c) for c in chunks]
    # gather 默认捕获每个任务的异常并 raise；NER 内部已做软失败兜底，
    # 这里无需 return_exceptions=True
    return await asyncio.gather(*tasks)


# ──────────────────── 主流程 ────────────────────


async def ingest_file(
    milvus_client,
    neo4j_driver,
    file_path: Path,
    doc_type: str = "report",
) -> tuple[int, int]:
    """处理单个文件：切片 → NER → embed → 写 Milvus + Neo4j。

    Returns:
        (chunk 数, 实体数)
    """
    settings = get_settings()
    text = file_path.read_text(encoding="utf-8")
    document_id = file_path.stem  # 用文件名（无扩展）当 document_id

    chunks = split_document(text)
    if not chunks:
        logger.warning("%s 切片为空，跳过", file_path.name)
        return 0, 0

    logger.info("处理 %s：%d 个 chunk", file_path.name, len(chunks))

    # 1) NER：并发抽取每个 chunk 的实体
    logger.info("  → 跑 NER（并发 %d）...", _NER_CONCURRENCY)
    chunk_entities = await batch_ner(chunks)
    total_entities_raw = sum(len(es) for es in chunk_entities)
    logger.info("  → NER 完成：共 %d 个实体提及（含重复）", total_entities_raw)

    # 2) Embedding：批量把所有 chunk 喂给 Embedding API
    logger.info("  → 跑 Embedding（%d 条）...", len(chunks))
    vectors = await aembed_texts(chunks)

    # 3) 组装 Milvus rows
    ingested_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    for idx, (chunk_text, vec, entities) in enumerate(
        zip(chunks, vectors, chunk_entities)
    ):
        # entity_tags 取实体名列表（去重），Milvus ARRAY<VARCHAR> 容量上限 50
        tag_names: list[str] = []
        seen_names: set[str] = set()
        for e in entities:
            n = e["name"]
            if n in seen_names:
                continue
            seen_names.add(n)
            tag_names.append(n)
            if len(tag_names) >= 50:
                break

        rows.append(
            {
                "chunk_id": make_chunk_id(document_id, idx),
                "vector": vec,
                "document_id": document_id,
                "content": chunk_text,
                "allowed_roles": ["ALL"],  # V1.0 全局可见
                "entity_tags": tag_names,
                "metadata": {
                    "type": doc_type,
                    "source": file_path.name,
                    "ingested_at": ingested_at,
                    "chunk_index": idx,
                },
            }
        )

    # 4) Milvus upsert（重跑幂等）
    milvus_client.upsert(
        collection_name=settings.milvus_collection,
        data=rows,
    )
    logger.info("  → Milvus upsert 完成：%d 条", len(rows))

    # 5) Neo4j 写入：Document + 批量 Entity + 批量 MENTIONED_IN
    await upsert_document(
        neo4j_driver,
        document_id=document_id,
        title=file_path.name,
        created_at=ingested_at,
    )

    # 全文档级去重：(name, type) 复合键
    entity_rows: list[dict] = []
    link_rows: list[dict] = []
    seen_entity_keys: set[tuple[str, str]] = set()

    for idx, entities in enumerate(chunk_entities):
        chunk_id = rows[idx]["chunk_id"]
        for e in entities:
            key = (e["name"], e["type"])
            if key not in seen_entity_keys:
                seen_entity_keys.add(key)
                entity_rows.append(
                    {
                        "name": e["name"],
                        "type": e["type"],
                        "document_id": document_id,
                    }
                )
            # 关系按 chunk 粒度建（关系按 chunk_id 去重）
            link_rows.append(
                {
                    "name": e["name"],
                    "type": e["type"],
                    "document_id": document_id,
                    "chunk_id": chunk_id,
                }
            )

    if entity_rows:
        await bulk_upsert_entities(neo4j_driver, entity_rows)
        await bulk_link_entities_to_chunk(neo4j_driver, link_rows)
        logger.info(
            "  → Neo4j 写入完成：%d 个实体节点，%d 条 MENTIONED_IN 关系",
            len(entity_rows),
            len(link_rows),
        )
    else:
        logger.info("  → Neo4j 跳过：本文档未抽取到实体")

    return len(rows), len(entity_rows)


async def main():
    setup_logging(debug=True)
    settings = get_settings()
    seed_dir = Path(_PROJECT_ROOT) / "data" / "seed"

    if not seed_dir.exists():
        logger.error("种子数据目录不存在：%s", seed_dir)
        return

    files = sorted(seed_dir.glob("*.txt"))
    if not files:
        logger.error("%s 下没有 .txt 文件", seed_dir)
        return

    logger.info("=== RAG + KG 入库开始 ===")
    logger.info("Milvus URI: %s", settings.milvus_uri)
    logger.info("Milvus Collection: %s", settings.milvus_collection)
    logger.info("Neo4j URI: %s", settings.neo4j_uri)
    logger.info("Embedding model: %s", settings.embedding_model)
    logger.info("NER model: %s", settings.kg_ner_model or settings.litellm_model)
    logger.info("种子文件: %d", len(files))

    milvus_client = init_milvus()
    neo4j_driver = await init_neo4j()

    try:
        total_chunks = 0
        total_entities = 0
        for f in files:
            cn, en = await ingest_file(
                milvus_client, neo4j_driver, f, doc_type="report"
            )
            total_chunks += cn
            total_entities += en
        logger.info(
            "=== 入库完成：%d 条 chunk，%d 个唯一实体 ===",
            total_chunks,
            total_entities,
        )
    finally:
        await close_neo4j()
        close_milvus()


if __name__ == "__main__":
    asyncio.run(main())
