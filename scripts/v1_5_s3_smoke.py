"""V1.5 S3.2 文件入库端到端 smoke 脚本（手动跑）。

前置条件：
1. PostgreSQL 起着（docker compose up -d postgres）
2. Redis 起着（docker compose up -d redis）
3. Milvus 起着（docker compose up -d standalone）
4. Neo4j 起着（docker compose up -d neo4j）
5. Celery worker 起着：
       celery -A app.tasks.celery_app worker --pool=solo -l info
6. EMBEDDING_API_KEY / LITELLM_API_KEY 配好

跑法：
    python scripts/v1_5_s3_smoke.py

流程：
  1) FastAPI 直连 lifespan 起所有组件
  2) 建临时 KB
  3) 生成临时 PDF + 上传（FILE-01）
  4) 轮询 GET /files/{file_id} 等 progress=100（最多 5 分钟）
  5) 查 Milvus 看切片真在 + 查 Neo4j 看 Document 节点在
  6) 删文件（FILE-04）→ 验 Milvus 切片消失 + Neo4j 节点消失
  7) 删 KB（KB-05）→ 验 Milvus Collection 消失 + 磁盘目录消失
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# 让脚本能 import app.*
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("v1_5_s3_smoke")

# ──────────────── 配置 ────────────────

BASE_URL = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:8000")
# 真实气象 PDF 论文可能 20-50 页，几百个 chunk × NER 2-3s × embedding 几秒，
# 整体可能 10 分钟以上；超时给宽松点
POLL_TIMEOUT_SECONDS = int(os.getenv("SMOKE_POLL_TIMEOUT", "900"))
POLL_INTERVAL_SECONDS = 2
# 单一 progress 阶段超过这个秒数没推进 → 打印警告（worker 大概率卡死）
PROGRESS_STUCK_WARN_SECONDS = 180

# 真实测试文档目录（用户放置）；若存在则优先用，否则现场生成 PDF
# 注意：实际目录名是 metaorological（缺 e），与 data/seed/ 同级
REAL_DOCS_DIR = Path(__file__).resolve().parents[1] / "data" / "metaorological"
# 优先级：PDF > docx > md > txt；选第一个匹配
REAL_DOC_EXT_PRIORITY = (".pdf", ".docx", ".md", ".txt")


def _ensure_unwrap(resp: httpx.Response) -> Any:
    """ApiResponse 统一格式：{code, message, data}；非 0 抛错。"""
    if resp.status_code >= 400:
        raise RuntimeError(
            f"HTTP {resp.status_code}: {resp.text[:500]}"
        )
    body = resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"API 错误 code={body['code']} message={body['message']}")
    return body["data"]


def _pick_real_doc() -> Path | None:
    """从 data/metaorological/ 挑一份真实测试文档，按扩展名优先级。

    没有目录或目录为空 → 返 None，由 _build_pdf 兜底现场生成。
    """
    if not REAL_DOCS_DIR.exists():
        return None
    candidates = list(REAL_DOCS_DIR.iterdir())
    if not candidates:
        return None
    # 按优先级挑
    for ext in REAL_DOC_EXT_PRIORITY:
        for p in candidates:
            if p.is_file() and p.suffix.lower() == ext:
                return p
    return None


def _build_pdf() -> Path:
    """fallback：现场生成最小 PDF（仅当 data/metaorological/ 为空时用）。

    用 tempfile.gettempdir() 跨平台（Windows 上会是 %TEMP%，Linux 上是 /tmp）。
    """
    import fitz

    out = Path(tempfile.gettempdir()) / f"smoke_test_{int(time.time())}.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (50, 80),
        "S3.2 端到端测试文档：中国上海是国际化大都市。",
        fontname="china-s",
        fontsize=12,
    )
    page = doc.new_page()
    page.insert_text(
        (50, 80),
        "DeepSeek 是一家位于杭州的人工智能公司，2025 年发布 V4 模型。",
        fontname="china-s",
        fontsize=12,
    )
    doc.save(str(out))
    doc.close()
    logger.info("smoke PDF 已现场生成（fallback）: %s", out)
    return out


def _resolve_test_doc() -> tuple[Path, bool]:
    """返 (路径, 是否需要清理临时文件)。

    优先用 data/metaorological/ 下的真文档；找不到才现场生成 PDF。
    """
    real = _pick_real_doc()
    if real:
        logger.info("使用真实测试文档: %s", real.name)
        return real, False
    logger.info("data/metaorological/ 无可用文档，现场生成 PDF")
    return _build_pdf(), True


async def _wait_for_completion(
    client: httpx.AsyncClient, kb_id: str, file_id: str
) -> dict:
    """轮询直到 status=completed / failed / 超时。

    同时检测"progress 卡在同一阶段超过 PROGRESS_STUCK_WARN_SECONDS"时打 WARNING，
    方便用户尽早发现 worker 卡死（不需要等总超时到才知道）。
    """
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    last_progress = -1
    last_progress_changed_at = time.time()
    stuck_warned = False

    while time.time() < deadline:
        resp = await client.get(
            f"/api/v1/knowledge-bases/{kb_id}/files/{file_id}"
        )
        data = _ensure_unwrap(resp)

        # progress 推进
        if data["progress"] != last_progress:
            logger.info(
                "轮询: status=%s progress=%d chunks=%d entities=%d",
                data["status"],
                data["progress"],
                data["chunk_count"],
                data["entity_count"],
            )
            last_progress = data["progress"]
            last_progress_changed_at = time.time()
            stuck_warned = False
        elif (
            not stuck_warned
            and time.time() - last_progress_changed_at > PROGRESS_STUCK_WARN_SECONDS
        ):
            logger.warning(
                "⚠ progress=%d 已卡 %d 秒（超 %ds）；请查 worker 日志看是不是阻塞了",
                data["progress"],
                int(time.time() - last_progress_changed_at),
                PROGRESS_STUCK_WARN_SECONDS,
            )
            stuck_warned = True

        if data["status"] == "completed":
            return data
        if data["status"] == "failed":
            raise RuntimeError(f"入库失败: {data.get('error_message')}")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(
        f"入库超时（{POLL_TIMEOUT_SECONDS}s）；progress 最终停在 {last_progress}。"
        "查 worker 日志看具体阻塞点。"
    )


def _check_milvus_chunks_exist(kb_id: str, document_id: str) -> int:
    """查 Milvus 看切片数量。"""
    from app.rag.milvus_client import init_milvus
    from app.rag.naming import build_kb_collection_name
    import uuid

    client = init_milvus()
    collection = build_kb_collection_name(uuid.UUID(kb_id))
    if not client.has_collection(collection):
        return 0
    rows = client.query(
        collection_name=collection,
        filter=f'document_id == "{document_id}"',
        output_fields=["chunk_id"],
        limit=1000,
    )
    return len(rows)


async def _check_neo4j_document_exists(kb_id: str, document_id: str) -> bool:
    from app.kg.neo4j_client import init_neo4j

    driver = await init_neo4j()
    cypher = (
        "MATCH (d:Document {document_id: $doc_id, kb_id: $kb_id}) RETURN d"
    )
    settings = __import__("app.core.config", fromlist=["get_settings"]).get_settings()
    async with driver.session(database=settings.neo4j_database) as sess:
        result = await sess.run(cypher, doc_id=document_id, kb_id=kb_id)
        rec = await result.single()
        return rec is not None


def _milvus_collection_exists(kb_id: str) -> bool:
    from app.rag.milvus_client import init_milvus
    from app.rag.naming import build_kb_collection_name
    import uuid

    client = init_milvus()
    return client.has_collection(build_kb_collection_name(uuid.UUID(kb_id)))


def _upload_dir_for_kb(kb_id: str) -> Path:
    from app.core.config import get_settings

    return Path(get_settings().upload_dir) / kb_id


# ──────────────── 主流程 ────────────────


async def main() -> None:
    logger.info("=== V1.5 S3.2 smoke 开始 base_url=%s ===", BASE_URL)

    # trust_env=False：忽略 HTTP_PROXY / HTTPS_PROXY 环境变量，
    # 直连 127.0.0.1。避免 Clash / 公司代理把本地请求转出去回不来变 502。
    async with httpx.AsyncClient(
        base_url=BASE_URL, timeout=30, trust_env=False
    ) as client:
        # ── 0) 预检：直连 /health 验证 FastAPI 真在 8000 端口 ──────────
        logger.info("[0/7] 预检 /health")
        try:
            resp = await client.get("/health")
        except httpx.RequestError as e:
            raise RuntimeError(
                f"连不上 {BASE_URL}/health：{e}\n"
                "排查清单：\n"
                "  - uvicorn 是否真起着？netstat -ano | findstr :8000\n"
                "  - 是否被系统代理拦截？$env:NO_PROXY='127.0.0.1,localhost'"
            ) from e
        if resp.status_code != 200:
            raise RuntimeError(
                f"/health 返回 {resp.status_code}（期望 200）。"
                "如果是 502 大概率被反向代理拦截，"
                "试 $env:NO_PROXY='127.0.0.1,localhost' 后重跑。\n"
                f"body={resp.text[:200]}"
            )
        logger.info("    ✓ FastAPI 健康")

        # ── 1) 建 KB ────────────────────────────────────────
        kb_name = f"smoke-test-{int(time.time())}"
        logger.info("[1/7] 创建知识库 name=%r", kb_name)
        resp = await client.post(
            "/api/v1/knowledge-bases",
            json={"name": kb_name, "description": "S3.2 smoke test"},
        )
        kb = _ensure_unwrap(resp)
        kb_id = kb["id"]
        logger.info("    ✓ kb_id=%s", kb_id)

        # ── 2) 选用真文档 / 现场生成 → 上传 ────────────────
        logger.info("[2/7] 选择并上传测试文档")
        test_doc, is_temp = _resolve_test_doc()
        # 按扩展名定 MIME（FastAPI multipart 解析需要正确 content-type）
        mime_map = {
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".md": "text/markdown",
            ".txt": "text/plain",
        }
        mime = mime_map.get(test_doc.suffix.lower(), "application/octet-stream")
        with open(test_doc, "rb") as f:
            resp = await client.post(
                f"/api/v1/knowledge-bases/{kb_id}/files",
                files={"file": (test_doc.name, f, mime)},
            )
        file_meta = _ensure_unwrap(resp)
        file_id = file_meta["id"]
        logger.info(
            "    ✓ file_id=%s task_id=%s filename=%r",
            file_id,
            file_meta.get("celery_task_id"),
            test_doc.name,
        )

        # ── 3) 轮询直到入库完成 ────────────────────────────
        logger.info("[3/7] 轮询入库进度（最长 5 分钟）")
        final = await _wait_for_completion(client, kb_id, file_id)
        assert final["progress"] == 100
        assert final["chunk_count"] > 0
        logger.info(
            "    ✓ 入库完成 chunks=%d entities=%d",
            final["chunk_count"],
            final["entity_count"],
        )

        # ── 4) 验 Milvus + Neo4j ────────────────────────────
        logger.info("[4/7] 验证 Milvus + Neo4j 真实写入")
        milvus_n = _check_milvus_chunks_exist(kb_id, file_id)
        neo4j_doc = await _check_neo4j_document_exists(kb_id, file_id)
        logger.info("    Milvus 切片数: %d", milvus_n)
        logger.info("    Neo4j Document 节点存在: %s", neo4j_doc)
        assert milvus_n == final["chunk_count"], "Milvus 切片数与 chunk_count 不一致"
        assert neo4j_doc, "Neo4j Document 节点应存在"

        # ── 5) FILE-04 删除文件 ─────────────────────────────
        logger.info("[5/7] 删除文件 file_id=%s", file_id)
        resp = await client.delete(
            f"/api/v1/knowledge-bases/{kb_id}/files/{file_id}"
        )
        _ensure_unwrap(resp)

        # 等一拍让清理生效
        await asyncio.sleep(1)
        milvus_n2 = _check_milvus_chunks_exist(kb_id, file_id)
        neo4j_doc2 = await _check_neo4j_document_exists(kb_id, file_id)
        logger.info("    删除后 Milvus 切片数: %d (期望 0)", milvus_n2)
        logger.info("    删除后 Neo4j Document 存在: %s (期望 False)", neo4j_doc2)
        assert milvus_n2 == 0, "FILE-04 后 Milvus 切片应清空"
        assert not neo4j_doc2, "FILE-04 后 Neo4j Document 应被删"

        # ── 6) KB-05 删除知识库 ─────────────────────────────
        logger.info("[6/7] 删除知识库 kb_id=%s", kb_id)
        resp = await client.delete(f"/api/v1/knowledge-bases/{kb_id}")
        _ensure_unwrap(resp)

        coll_exists = _milvus_collection_exists(kb_id)
        kb_dir = _upload_dir_for_kb(kb_id)
        logger.info("    删除后 Milvus Collection 存在: %s (期望 False)", coll_exists)
        logger.info("    删除后 KB 上传目录存在: %s (期望 False)", kb_dir.exists())
        assert not coll_exists, "KB-05 后 Milvus Collection 应消失"
        assert not kb_dir.exists(), "KB-05 后 KB 上传目录应清空"

        # ── 7) 清理本测产物 ────────────────────────────────
        logger.info("[7/7] 清理临时文件")
        if is_temp:
            test_doc.unlink(missing_ok=True)
        else:
            logger.info("    （用的是真实文档 %s，保留不删）", test_doc.name)

    logger.info("=== ✓ V1.5 S3.2 smoke 全部通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
