"""V2.0 Hermes 全链路集成 smoke。

前置：
- docker compose up -d（PG + Milvus + Neo4j + Redis）
- uvicorn app.main:app --reload
- celery -A app.tasks.celery_app worker --pool=solo -l info
- .env 配置 LITELLM_MODEL / LITELLM_API_KEY / EMBEDDING_MODEL / EMBEDDING_API_KEY

跑法：
  python scripts/v2_smoke.py
  python scripts/v2_smoke.py --skip-faithfulness   # 跳过自检节省 LLM 成本
  python scripts/v2_smoke.py --skip-cleanup        # 跑完不删 KB 便于人工查 trace

覆盖 V2.0 P0/P1/P2 全链路：
  T0 V2 Schema + V2 KB Collection
  T1 IDP-01/02 结构感知解析 + 切片
  T2 BM25 + RRF 混合检索
  T3 Trace 采集 + 查询接口
  T4 Reranker（可选，依赖 RERANKER_TYPE）
  T5 Citation 注入 + 解析
  T6 /api/v2/query 统一查询接口
  T7 IDP-03/04/05 表格描述 + 双层索引 + 文档元数据
  T8 HRE-01/02/06 Query 改写 + Query NER + 三层配置合并
  T9 CHC-03/04 置信度评分 + 答案自检
  T10 UQA-02/03/04 分层子接口（/retrieve、/generate、/rerank）
  T12 OBS-03 聚合统计（/api/v2/analytics）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("v2_smoke")

BASE_URL = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:8000")
POLL_TIMEOUT = int(os.getenv("SMOKE_POLL_TIMEOUT", "1500"))  # 大文档 + IDP 多 LLM 调用
POLL_INTERVAL = 3

REAL_DOCS_DIR = Path(__file__).resolve().parents[1] / "data" / "metaorological"


# ──────────────────── HTTP 工具 ────────────────────


def _ensure(resp: httpx.Response) -> Any:
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
    body = resp.json()
    # /api/v2 endpoints 默认走 ApiResponse 包装；直接走 FastAPI 返回的不一定有 code 字段
    if isinstance(body, dict) and body.get("code") is not None:
        if body["code"] != 0:
            raise RuntimeError(f"API 错 code={body['code']} message={body['message']}")
        return body["data"]
    return body


def _pick_real_docs() -> list[Path]:
    if not REAL_DOCS_DIR.exists():
        return []
    return sorted(
        p for p in REAL_DOCS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in {".pdf", ".docx", ".md", ".txt"}
    )


def _mime_for(path: Path) -> str:
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".md": "text/markdown",
        ".txt": "text/plain",
    }.get(path.suffix.lower(), "application/octet-stream")


# ──────────────────── 入库轮询 ────────────────────


async def _wait_completion(client: httpx.AsyncClient, kb_id: str, file_id: str) -> dict:
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
        elif time.time() - last_change > 240:
            logger.warning("  ⚠ progress=%d 卡 240s 无推进", d["progress"])
            last_change = time.time()
        if d["status"] == "completed":
            return d
        if d["status"] == "failed":
            raise RuntimeError(f"入库失败: {d.get('error_message')}")
        await asyncio.sleep(POLL_INTERVAL)
    raise TimeoutError(f"入库超时 {POLL_TIMEOUT}s")


# ──────────────────── 验收断言 ────────────────────


def _assert_v2_query_response(name: str, resp: dict, *, expect_unverified: bool | None = None) -> None:
    """V2.0 query 响应字段完备性断言。"""
    required = [
        "answer", "source_citations", "trace_id", "total_latency_ms",
        "confidence", "faithfulness_check",
    ]
    for k in required:
        assert k in resp, f"[{name}] 响应缺字段 {k}"

    # confidence 范围
    if resp["confidence"] is not None:
        assert 0.0 <= resp["confidence"] <= 1.0, f"[{name}] confidence 越界"

    # faithfulness_check 三态
    assert resp["faithfulness_check"] in ("ok", "skipped", "disabled"), \
        f"[{name}] faithfulness_check={resp['faithfulness_check']!r} 非法"

    # 期望 unverified
    if expect_unverified is True:
        assert resp.get("unverified_claims"), f"[{name}] 期望有 unverified_claims"


# ──────────────────── 主流程 ────────────────────


async def run(args) -> None:
    logger.info("=== V2.0 Hermes 全链路 smoke 开始 base=%s ===", BASE_URL)

    async with httpx.AsyncClient(
        base_url=BASE_URL, timeout=180, trust_env=False,
    ) as client:
        # ──────── 0) 预检 ────────
        r = await client.get("/health")
        assert r.status_code == 200, "FastAPI 未启动"
        logger.info("[0] FastAPI 健康 ✓")

        # ──────── 1) 建一个 V2 KB ────────
        kb_name = f"v2-smoke-{int(time.time())}"
        r = await client.post(
            "/api/v1/knowledge-bases",
            json={"name": kb_name, "description": "V2.0 集成 smoke"},
        )
        kb = _ensure(r)
        kb_id = kb["id"]
        logger.info("[1] KB 已建 id=%s name=%s", kb_id, kb_name)

        # ──────── 1b) HRE-06：用 PATCH 设 KB 级 retrieval_config ────────
        retrieval_config = {
            "top_k": 5,
            "enable_graph_rag": True,
            "bm25_enable": True,
        }
        r = await client.patch(
            f"/api/v1/knowledge-bases/{kb_id}",
            json={"retrieval_config": retrieval_config},
        )
        d = _ensure(r)
        assert d.get("retrieval_config") == retrieval_config, "KB.retrieval_config 写入失败"
        logger.info("[1b] HRE-06 KB.retrieval_config 已写入 ✓")

        # ──────── 2) 上传 1 份真实文档（含 IDP-03 表格识别覆盖最丰富） ────────
        docs = _pick_real_docs()
        if not docs:
            raise RuntimeError(
                f"未找到真实文档目录 {REAL_DOCS_DIR}；至少放一份 pdf/docx/md/txt"
            )
        # 优先选 docx（更容易含表格），然后 pdf
        doc = next((p for p in docs if p.suffix.lower() == ".docx"), docs[0])
        logger.info("[2] 准备上传文档: %s", doc.name)

        with open(doc, "rb") as f:
            r = await client.post(
                f"/api/v1/knowledge-bases/{kb_id}/files",
                files={"file": (doc.name, f, _mime_for(doc))},
            )
        d = _ensure(r)
        file_id = d["id"]
        logger.info("[2] 文件已上传 file_id=%s", file_id)

        # ──────── 3) 轮询入库（IDP-03/04/05 + NER + Milvus + Neo4j） ────────
        logger.info("[3] 等入库完成（含 IDP-03 表格描述 + IDP-04 双层索引 + IDP-05 元数据）")
        final = await _wait_completion(client, kb_id, file_id)
        logger.info(
            "[3] 入库完成 chunk_count=%d entity_count=%d completed_at=%s",
            final["chunk_count"], final["entity_count"], final.get("completed_at"),
        )
        assert final["chunk_count"] > 0, "chunk_count 应 > 0"

        # IDP-05 验收：summary_brief / doc_metadata 已写入
        if final.get("summary_brief"):
            logger.info("[3a] IDP-05 summary_brief 已写入: %r",
                        final["summary_brief"][:80])
        else:
            logger.warning("[3a] ⚠ IDP-05 summary_brief 为空（可能 LLM 软失败）")

        if final.get("doc_metadata"):
            logger.info("[3a] IDP-05 doc_metadata: %s",
                        json.dumps(final["doc_metadata"], ensure_ascii=False)[:200])
        else:
            logger.warning("[3a] ⚠ IDP-05 doc_metadata 为空（可能 LLM 软失败）")

        # ──────── 4) 文件列表（FILE-02）：summary_brief 暴露验证 ────────
        r = await client.get(f"/api/v1/knowledge-bases/{kb_id}/files")
        d = _ensure(r)
        items = d.get("items", [])
        assert items and items[0].get("id") == file_id
        logger.info(
            "[4] 文件列表暴露 summary_brief=%r ✓",
            items[0].get("summary_brief", "")[:60],
        )

        # ──────── 5) 跑 4 类 V2 query 验证不同特性 ────────
        # 5a：基础查询（none 改写 + Graph RAG 默认开 + 自检默认关）
        logger.info("[5a] 基础查询：query_rewrite=none，自检默认关")
        r = await client.post(
            "/api/v2/query",
            json={
                "kb_ids": [kb_id],
                "query": "这份文档主要讲了什么？",
                "options": {},
            },
        )
        resp_a = _ensure(r)
        _assert_v2_query_response("5a", resp_a)
        logger.info(
            "  ✓ trace_id=%s confidence=%.3f answer_len=%d source_citations=%d "
            "faithfulness=%s ner=%s graph_tags=%s latency=%dms",
            (resp_a["trace_id"] or "")[:8],
            resp_a["confidence"] or 0,
            len(resp_a["answer"]),
            len(resp_a["source_citations"]),
            resp_a["faithfulness_check"],
            len(resp_a.get("ner_entities") or []),
            len(resp_a.get("graph_anchored_tags") or []),
            resp_a["total_latency_ms"] or 0,
        )

        # 5b：HRE-01 HyDE 改写
        logger.info("[5b] HyDE 改写：query_rewrite=hyde")
        r = await client.post(
            "/api/v2/query",
            json={
                "kb_ids": [kb_id],
                "query": "气象卫星在监测中扮演什么角色？",
                "options": {"query_rewrite": "hyde"},
            },
        )
        resp_b = _ensure(r)
        _assert_v2_query_response("5b", resp_b)
        rewritten = resp_b.get("rewritten_query")
        logger.info(
            "  ✓ rewritten_query 长度=%d confidence=%.3f",
            len(rewritten or ""), resp_b["confidence"] or 0,
        )
        if not rewritten:
            logger.warning("  ⚠ rewritten_query 为空（LLM 改写软失败）")

        # 5c：HRE-01 multi_query 改写
        logger.info("[5c] multi_query 改写：query_rewrite=multi_query")
        r = await client.post(
            "/api/v2/query",
            json={
                "kb_ids": [kb_id],
                "query": "卫星遥感技术的应用",
                "options": {"query_rewrite": "multi_query"},
            },
        )
        resp_c = _ensure(r)
        _assert_v2_query_response("5c", resp_c)
        sub_qs = resp_c.get("sub_queries") or []
        logger.info(
            "  ✓ sub_queries=%d 条 / confidence=%.3f / 引用=%d",
            len(sub_qs), resp_c["confidence"] or 0, len(resp_c["source_citations"]),
        )

        # 5d：CHC-04 答案自检（默认 False，这里显式打开）
        if not args.skip_faithfulness:
            logger.info("[5d] CHC-04 答案自检：enable_faithfulness_check=true")
            r = await client.post(
                "/api/v2/query",
                json={
                    "kb_ids": [kb_id],
                    "query": "这份文档发布于 2030 年 12 月，作者是谁？",  # 故意问可能编造的细节
                    "options": {
                        "query_rewrite": "none",
                        "enable_faithfulness_check": True,
                    },
                },
            )
            resp_d = _ensure(r)
            _assert_v2_query_response("5d", resp_d)
            faith = resp_d["faithfulness_check"]
            unv = resp_d.get("unverified_claims") or []
            logger.info(
                "  ✓ faithfulness_check=%s unverified_claims=%d "
                "confidence=%.3f answer_len=%d",
                faith, len(unv), resp_d["confidence"] or 0, len(resp_d["answer"]),
            )
            if faith == "ok" and unv:
                logger.info(
                    "  ✓ 自检发现 %d 条未支撑事实；answer 末尾应含 ⚠ 警告: %s",
                    len(unv), "⚠" in resp_d["answer"],
                )
            elif faith == "skipped":
                logger.warning("  ⚠ 自检软失败 skipped（可能 LLM 超时）")
        else:
            logger.info("[5d] 已跳过自检（--skip-faithfulness）")

        # ──────── 6) Trace 链路完整性（OBS-02） ────────
        logger.info("[6] 验 Trace step 链完整")
        trace_id = resp_a["trace_id"]
        r = await client.get(f"/api/v2/traces/{trace_id}")
        trace = _ensure(r)
        steps = trace.get("steps", [])
        step_types = [s["step_type"] for s in steps]
        logger.info("  trace 步骤序列：%s", " → ".join(step_types))
        # 必含 V2 关键步骤
        for must in ("query_rewrite", "query_ner", "graph_anchor",
                     "retrieve", "build_context", "generate", "citation_parse"):
            if must in step_types:
                logger.info("  ✓ %s", must)
            else:
                logger.warning("  ⚠ trace 缺步骤 %s", must)

        # 自检 trace（5d 跑过且 ok 时）
        if not args.skip_faithfulness:
            r = await client.get(f"/api/v2/traces/{resp_d['trace_id']}")
            trace_d = _ensure(r)
            d_step_types = [s["step_type"] for s in trace_d.get("steps", [])]
            if "faithfulness_check" in d_step_types:
                logger.info("  ✓ 5d trace 含 faithfulness_check 步骤")
            else:
                logger.warning("  ⚠ 5d trace 缺 faithfulness_check")

        # ──────── 8) T10 分层子接口（UQA-02/03/04） ────────
        # 8a) UQA-02 /v2/retrieve（不调 LLM 的纯检索）
        logger.info("[8a] UQA-02 /v2/retrieve：不调 LLM 的纯检索")
        r = await client.post(
            "/api/v2/retrieve",
            json={
                "query": "这份文档主要讲了什么",
                "kb_ids": [kb_id],
                "top_k": 5,
                "rerank": False,         # 关 rerank 走纯混合检索
                "enable_graph_rag": False,
            },
        )
        retrieve_resp = _ensure(r)
        assert "chunks" in retrieve_resp, "[8a] /retrieve 响应缺 chunks"
        assert "total_retrieved" in retrieve_resp, "[8a] /retrieve 响应缺 total_retrieved"
        chunks = retrieve_resp["chunks"]
        logger.info(
            "  ✓ /retrieve 返回 %d 条 chunks（total_retrieved=%d after_rerank=%d latency=%dms）",
            len(chunks),
            retrieve_resp.get("total_retrieved", 0),
            retrieve_resp.get("after_rerank", 0),
            retrieve_resp.get("total_latency_ms") or 0,
        )
        if chunks:
            first = chunks[0]
            for k in ("chunk_id", "content", "vector_score"):
                assert k in first, f"[8a] chunks[0] 缺字段 {k}"
            logger.info(
                "  ✓ 首条 chunk_id=%s vector_score=%.3f bm25_score=%s rrf_score=%s",
                first.get("chunk_id"),
                first.get("vector_score") or 0,
                first.get("bm25_score"),
                first.get("rrf_score"),
            )

        # 8b) UQA-04 /v2/rerank（独立精排端点）
        logger.info("[8b] UQA-04 /v2/rerank：独立精排端点")
        rerank_candidates = [
            {"id": "c1", "text": "气象卫星用于监测地球表面与大气层"},
            {"id": "c2", "text": "今天的午餐吃什么"},
            {"id": "c3", "text": "数值天气预报基于流体力学方程"},
        ]
        r = await client.post(
            "/api/v2/rerank",
            json={
                "query": "气象卫星",
                "candidates": rerank_candidates,
                "top_n": 3,
            },
        )
        rerank_resp = _ensure(r)
        assert "results" in rerank_resp, "[8b] /rerank 响应缺 results"
        results = rerank_resp["results"]
        assert len(results) >= 1, "[8b] /rerank 至少返回 1 条"
        logger.info(
            "  ✓ /rerank 返回 %d 条；首条 id=%s rerank_score=%.4f（latency=%dms）",
            len(results),
            results[0].get("id"),
            results[0].get("rerank_score") or 0,
            rerank_resp.get("total_latency_ms") or 0,
        )
        # 验降序
        scores = [r["rerank_score"] for r in results]
        assert scores == sorted(scores, reverse=True), "[8b] results 应按 rerank_score 降序"

        # 8c) UQA-03 /v2/generate（开发者自定义 context 跳过检索）
        logger.info("[8c] UQA-03 /v2/generate：自定义 context 跳过检索")
        custom_context = [
            {
                "chunk_id": "doc1_p1",
                "content": "气象卫星是搭载在人造地球卫星上的气象观测仪器，"
                           "用于从太空对地球大气和地表进行连续、大范围的观测。",
                "source_label": "气象学导论_P12",
            },
            {
                "chunk_id": "doc1_p2",
                "content": "极轨卫星轨道倾角接近 90 度，每天可对全球进行 2 次完整覆盖；"
                           "静止卫星位于赤道上空 36000 公里处，可对同一区域持续观测。",
                "source_label": "气象学导论_P15",
            },
        ]
        r = await client.post(
            "/api/v2/generate",
            json={
                "query": "气象卫星有哪几类轨道？分别有什么特点？",
                "context_chunks": custom_context,
                "options": {
                    "enable_citation": True,
                    "enable_faithfulness_check": False,
                },
            },
        )
        gen_resp = _ensure(r)
        for k in ("answer", "source_citations", "confidence", "faithfulness_check"):
            assert k in gen_resp, f"[8c] /generate 响应缺字段 {k}"
        assert len(gen_resp["answer"]) > 0, "[8c] answer 不应为空"
        logger.info(
            "  ✓ /generate answer_len=%d citations=%d confidence=%.3f faithfulness=%s",
            len(gen_resp["answer"]),
            len(gen_resp["source_citations"] or []),
            gen_resp.get("confidence") or 0,
            gen_resp.get("faithfulness_check"),
        )

        # ──────── 7) 清理 ────────
        if not args.skip_cleanup:
            logger.info("[7] 清理 KB（Milvus + PG + Neo4j 三联）")
            r = await client.delete(f"/api/v1/knowledge-bases/{kb_id}")
            assert r.status_code in (200, 204), f"KB 删失败 {r.status_code}"
            logger.info("[7] KB 已删 ✓")
        else:
            logger.info("[7] 已跳过清理（--skip-cleanup），保留 kb_id=%s", kb_id)

    logger.info("=== ✓ V2.0 全链路 smoke 通过 ===")


def main() -> None:
    parser = argparse.ArgumentParser(description="V2.0 Hermes 全链路集成 smoke")
    parser.add_argument(
        "--skip-faithfulness", action="store_true",
        help="跳过 CHC-04 答案自检节省 LLM 成本",
    )
    parser.add_argument(
        "--skip-cleanup", action="store_true",
        help="跑完不删 KB 便于人工查 trace / 数据排查",
    )
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
