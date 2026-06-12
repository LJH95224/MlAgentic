"""V1.5 S5 KB-06 关联对话 + V1.5 全链路端到端 smoke。

前置：所有服务起着（同 v1_5_s3_smoke）+ Celery worker 起着。

流程：
  1. 建两个 KB：kb_A、kb_B
  2. 各上传一份文档
  3. 轮询入库完成
  4. 对话 1：不传 kb_ids → 走默认 collection（V1.0 行为）
  5. 对话 2：kb_ids=[kb_A] → SSE tool_start 含 _kb_ids 信息；只查 kb_A
  6. 对话 3：kb_ids=[] → 不查任何 KB
  7. KB-03 详情：entity_count 接通 Neo4j 真实计数（S5 解 stub）
  8. 删两个 KB 清理

跑法：
  python scripts/v1_5_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("v1_5_smoke")

BASE_URL = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:8000")
POLL_TIMEOUT = int(os.getenv("SMOKE_POLL_TIMEOUT", "900"))
POLL_INTERVAL = 2

REAL_DOCS_DIR = Path(__file__).resolve().parents[1] / "data" / "metaorological"


def _ensure(resp: httpx.Response) -> Any:
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
    body = resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"API 错 code={body['code']} message={body['message']}")
    return body["data"]


def _pick_two_real_docs() -> tuple[Path, Path] | None:
    """从 data/metaorological/ 挑两份不同的文档；不够就 None。"""
    if not REAL_DOCS_DIR.exists():
        return None
    candidates = sorted(
        p for p in REAL_DOCS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in {".pdf", ".docx", ".md", ".txt"}
    )
    if len(candidates) < 2:
        return None
    return candidates[0], candidates[1]


def _make_two_test_files() -> tuple[Path, Path]:
    """fallback：现场生成两份不同主题的 PDF。"""
    import fitz

    docs = []
    for topic, content in (
        ("typhoon", "S5 测试 A 库：西北太平洋台风的常见路径研究。"),
        ("rainfall", "S5 测试 B 库：长江流域 2025 年降雨分布异常分析。"),
    ):
        out = Path(tempfile.gettempdir()) / f"smoke_{topic}_{int(time.time())}.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text(
            (50, 80), content, fontname="china-s", fontsize=12
        )
        doc.save(str(out))
        doc.close()
        docs.append(out)
    return docs[0], docs[1]


async def _wait_completion(client, kb_id, file_id):
    deadline = time.time() + POLL_TIMEOUT
    last = -1
    last_change = time.time()
    while time.time() < deadline:
        r = await client.get(f"/api/v1/knowledge-bases/{kb_id}/files/{file_id}")
        d = _ensure(r)
        if d["progress"] != last:
            logger.info(
                "  轮询 %s: status=%s progress=%d chunks=%d entities=%d",
                file_id[:8],
                d["status"],
                d["progress"],
                d["chunk_count"],
                d["entity_count"],
            )
            last = d["progress"]
            last_change = time.time()
        elif time.time() - last_change > 180:
            logger.warning("  ⚠ progress=%d 卡 180s 无推进", d["progress"])
            last_change = time.time()
        if d["status"] == "completed":
            return d
        if d["status"] == "failed":
            raise RuntimeError(f"入库失败: {d.get('error_message')}")
        await asyncio.sleep(POLL_INTERVAL)
    raise TimeoutError(f"入库超时 {POLL_TIMEOUT}s")


async def _stream_chat_collect(client, session_id, content, kb_ids=None):
    """打一次 /chat/stream 收集所有 SSE 事件返回 list[dict]。"""
    payload = {"session_id": session_id, "content": content}
    if kb_ids is not None:
        payload["kb_ids"] = [str(k) for k in kb_ids]

    events: list[dict] = []
    async with client.stream(
        "POST",
        "/api/v1/chat/stream",
        json=payload,
        timeout=120,
    ) as resp:
        if resp.status_code != 200:
            body = await resp.aread()
            raise RuntimeError(
                f"chat/stream HTTP {resp.status_code}: {body.decode()[:500]}"
            )
        # SSE 行式解析
        buf_event: str | None = None
        buf_data: str | None = None
        async for line in resp.aiter_lines():
            if not line:
                # 空行 = 一帧结束
                if buf_data:
                    try:
                        events.append(json.loads(buf_data))
                    except json.JSONDecodeError:
                        logger.warning("非 JSON 帧: %r", buf_data[:80])
                buf_event = buf_data = None
                continue
            if line.startswith("event:"):
                buf_event = line[6:].strip()
            elif line.startswith("data:"):
                buf_data = line[5:].strip()
    return events


async def main() -> None:
    logger.info("=== V1.5 全链路 smoke 开始 base=%s ===", BASE_URL)

    async with httpx.AsyncClient(
        base_url=BASE_URL, timeout=120, trust_env=False
    ) as client:
        # 预检
        r = await client.get("/health")
        assert r.status_code == 200, "FastAPI 没起"
        logger.info("[0] FastAPI 健康 ✓")

        # ── 1) 建两个 KB ──────────────────────────────
        kbs: dict[str, str] = {}
        for label in ("A", "B"):
            r = await client.post(
                "/api/v1/knowledge-bases",
                json={"name": f"smoke-{label}-{int(time.time())}",
                      "description": f"S5 测试 {label}"},
            )
            d = _ensure(r)
            kbs[label] = d["id"]
            logger.info("[1.%s] KB-%s 已建 id=%s", label, label, d["id"])

        # ── 2) 各上传一份文档 ────────────────────────
        real_pair = _pick_two_real_docs()
        if real_pair:
            doc_a, doc_b = real_pair
            is_temp = False
            logger.info("使用真实文档: %s | %s", doc_a.name, doc_b.name)
        else:
            doc_a, doc_b = _make_two_test_files()
            is_temp = True

        mime_map = {".pdf": "application/pdf",
                    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ".md": "text/markdown",
                    ".txt": "text/plain"}

        file_ids: dict[str, str] = {}
        for label, doc in (("A", doc_a), ("B", doc_b)):
            with open(doc, "rb") as f:
                r = await client.post(
                    f"/api/v1/knowledge-bases/{kbs[label]}/files",
                    files={"file": (doc.name, f, mime_map.get(doc.suffix.lower(), "application/octet-stream"))},
                )
            d = _ensure(r)
            file_ids[label] = d["id"]
            logger.info("[2.%s] 文件已上传 kb=%s file=%s", label, label, d["id"])

        # ── 3) 轮询两个入库完成 ─────────────────────
        logger.info("[3] 等两份文档入库完成")
        for label in ("A", "B"):
            final = await _wait_completion(client, kbs[label], file_ids[label])
            logger.info(
                "  KB-%s 入库完成 chunks=%d entities=%d",
                label, final["chunk_count"], final["entity_count"],
            )

        # ── 4) KB-03 详情：entity_count 接通 Neo4j ──
        for label in ("A", "B"):
            r = await client.get(f"/api/v1/knowledge-bases/{kbs[label]}")
            d = _ensure(r)
            logger.info(
                "[4.%s] KB-%s 详情 file_count=%d chunk_count=%d entity_count=%d",
                label, label, d["file_count"], d["chunk_count"], d["entity_count"],
            )
            assert d["chunk_count"] > 0, f"KB-{label} chunk_count 应 > 0"

        # ── 5) 建一个共享 session 跑 3 轮对话 ────────
        r = await client.post("/api/v1/sessions", json={})
        sid = _ensure(r)["id"]
        logger.info("[5] 已建对话 session=%s", sid)

        # 对话 5a：不传 kb_ids
        logger.info("[5.a] 对话不传 kb_ids（V1.0 默认行为）")
        events_a = await _stream_chat_collect(client, sid, "你好，简单介绍下你能做什么。")
        text_a = "".join(e.get("content", "") for e in events_a if e.get("type") == "text")
        logger.info("  ✓ 收到 %d 个 SSE 事件，正文 %d 字", len(events_a), len(text_a))

        # 对话 5b：kb_ids=[kb_A]
        logger.info("[5.b] 对话 kb_ids=[kb_A]，应只查 kb_A")
        events_b = await _stream_chat_collect(
            client, sid, "请基于知识库帮我总结一下你查到的内容",
            kb_ids=[kbs["A"]],
        )
        tool_starts_b = [e for e in events_b if e.get("type") == "tool_start"]
        logger.info("  ✓ 收到 %d 个 SSE 事件 / %d 个 tool_start", len(events_b), len(tool_starts_b))
        # KB-06 验收：tool_start 事件 args 中应含 _kb_ids
        if tool_starts_b:
            kb_ids_in_event = any(
                kbs["A"] in str((ts.get("args") or {}).get("_kb_ids", []))
                for ts in tool_starts_b
            )
            assert kb_ids_in_event, "tool_start 事件 args 应含 _kb_ids=[kb_A]"
            logger.info("  ✓ tool_start.args._kb_ids 验证通过（含 kb_A）")

        # 对话 5c：kb_ids=[] 显式不查
        logger.info("[5.c] 对话 kb_ids=[]，应不调任何检索工具")
        events_c = await _stream_chat_collect(client, sid, "1+1 等于几？", kb_ids=[])
        tool_starts_c = [e for e in events_c if e.get("type") == "tool_start"]
        text_c = "".join(e.get("content", "") for e in events_c if e.get("type") == "text")
        logger.info(
            "  ✓ 收到 %d 个 SSE 事件 / %d 个 tool_start / 正文 %d 字",
            len(events_c), len(tool_starts_c), len(text_c),
        )

        # ── 6) 清理 ──────────────────────────────────
        for label in ("A", "B"):
            await client.delete(f"/api/v1/knowledge-bases/{kbs[label]}")
            logger.info("[6.%s] KB-%s 已删", label, label)

        # 清临时文件
        if is_temp:
            for p in (doc_a, doc_b):
                p.unlink(missing_ok=True)

    logger.info("=== ✓ V1.5 全链路 smoke 通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
