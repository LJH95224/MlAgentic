"""RAG 调优评估对比脚本（A.1 Reranker 调优专用）。

通过 /api/v2/knowledge-bases/{kb_id}/evaluate 端点串行跑多组实验，
轮询等评估完成后自动取 summary，最后输出横向对比报告。

前置：
  - docker compose up -d（PG + Milvus + Neo4j + Redis）
  - uvicorn app.main:app --reload
  - celery -A app.tasks.celery_app worker --pool=solo -l info
  - .env 配置好 LITELLM / EMBEDDING / RERANKER 相关变量
  - 目标 KB 已建好并完成文档入库

跑法：
  # 最简用法：指定 KB + 评估集 + 4 组实验配置
  python scripts/eval_compare.py --kb-id <UUID> --eval-set eval_set_smoke.json

  # 只跑 2 组快速验证
  python scripts/eval_compare.py --kb-id <UUID> --eval-set eval_set_smoke.json \
      --experiments baseline rerank_thresh_0.0

  # 自定义轮询超时
  python scripts/eval_compare.py --kb-id <UUID> --eval-set eval_set_smoke.json \
      --poll-timeout 600

输出：
  - 终端打印横向对比表格（4 项指标 + overall）
  - JSON 结果文件 eval_compare_<timestamp>.json 保存完整数据

实验配置说明：
  - baseline:              无 Reranker（reranker_enable=false）
  - rerank_thresh_0.1:     Reranker 开 + threshold=0.1
  - rerank_thresh_0.0:     Reranker 开 + threshold=0.0（纯精排不过滤）
  - rerank_bge_m3:         切换到 bge-reranker-v2-m3 + threshold=0.0
    （注：切模型需配合 RERANKER_MODEL 环境变量，脚本只设 threshold）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("eval_compare")

BASE_URL = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:8000")
POLL_INTERVAL = int(os.getenv("EVAL_POLL_INTERVAL", "5"))  # 秒
DEFAULT_POLL_TIMEOUT = int(os.getenv("EVAL_POLL_TIMEOUT", "900"))  # 15 分钟


# ──────────────────── 预定义实验配置 ────────────────────

EXPERIMENTS: dict[str, dict[str, Any]] = {
    "baseline": {
        "label": "A1 基线（无 Reranker）",
        "retrieval_options": {
            "reranker_enable": False,
            "bm25_enable": True,
            "enable_graph_rag": True,
            "query_rewrite": "none",
        },
    },
    "rerank_thresh_0.3": {
        "label": "B0 Reranker + threshold=0.3（当前默认）",
        "retrieval_options": {
            "reranker_enable": True,
            "similarity_threshold": 0.3,
            "bm25_enable": True,
            "enable_graph_rag": True,
            "query_rewrite": "none",
        },
    },
    "rerank_thresh_0.1": {
        "label": "B1 Reranker + threshold=0.1",
        "retrieval_options": {
            "reranker_enable": True,
            "similarity_threshold": 0.1,
            "bm25_enable": True,
            "enable_graph_rag": True,
            "query_rewrite": "none",
        },
    },
    "rerank_thresh_0.0": {
        "label": "B2 Reranker + threshold=0.0（纯精排不过滤）",
        "retrieval_options": {
            "reranker_enable": True,
            "similarity_threshold": 0.0,
            "bm25_enable": True,
            "enable_graph_rag": True,
            "query_rewrite": "none",
        },
    },
}


# ──────────────────── HTTP 工具 ────────────────────


def _ensure(resp: httpx.Response) -> Any:
    """检查 HTTP 响应，非 2xx 或 API code != 0 时抛异常。"""
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
    body = resp.json()
    if isinstance(body, dict) and body.get("code") is not None:
        if body["code"] != 0:
            raise RuntimeError(f"API 错 code={body['code']} message={body['message']}")
        return body["data"]
    return body


async def create_evaluation(
    client: httpx.AsyncClient,
    kb_id: str,
    eval_set: list[dict],
    retrieval_options: dict,
    name: str,
) -> str:
    """提交评估任务，返回 eval_task_id。"""
    payload = {
        "eval_set": eval_set,
        "retrieval_options": retrieval_options,
        "name": name,
    }
    r = await client.post(
        f"/api/v2/knowledge-bases/{kb_id}/evaluate",
        json=payload,
    )
    data = _ensure(r)
    eval_task_id = data["eval_task_id"]
    logger.info("  评估已提交: eval_task_id=%s name=%s", eval_task_id, name)
    return str(eval_task_id)


async def poll_evaluation(
    client: httpx.AsyncClient,
    kb_id: str,
    eval_task_id: str,
    timeout: int,
) -> dict:
    """轮询等评估完成，返回 GET /evaluations/{id} 的完整结果。"""
    deadline = time.time() + timeout
    last_progress = -1
    while time.time() < deadline:
        r = await client.get(
            f"/api/v2/knowledge-bases/{kb_id}/evaluations/{eval_task_id}"
        )
        data = _ensure(r)
        status = data.get("status", "unknown")
        progress = data.get("progress", 0)
        if progress != last_progress:
            logger.info("    轮询 %s: status=%s progress=%d%%", eval_task_id[:8], status, progress)
            last_progress = progress

        if status == "completed":
            return data
        if status == "failed":
            raise RuntimeError(
                f"评估任务失败: {data.get('error_message', 'unknown')}"
            )
        await asyncio.sleep(POLL_INTERVAL)

    raise TimeoutError(f"评估超时 {timeout}s (eval_task_id={eval_task_id})")


# ──────────────────── 报告输出 ────────────────────


def _fmt_score(v: float | None) -> str:
    """格式化指标分数：None → 'N/A'，否则保留 3 位小数。"""
    if v is None:
        return "N/A"
    return f"{v:.3f}"


def print_comparison_table(results: list[dict]) -> None:
    """打印横向对比表格。"""
    metrics = ["faithfulness", "answer_relevancy", "context_precision", "context_recall", "overall_score"]
    header = ["实验"] + metrics
    # 计算列宽
    col_w = [30] + [18] * len(metrics)

    # 表头
    line = "|" + "|".join(h.center(w) for h, w in zip(header, col_w)) + "|"
    sep = "|" + "|".join("-" * w for w in col_w) + "|"
    print("\n" + "=" * sum(col_w))
    print("[RAG] 调优评估对比报告")
    print("=" * sum(col_w))
    print(line)
    print(sep)

    # 找出各项最优
    best: dict[str, float] = {}
    for m in metrics:
        scores = [r["summary"].get(m) for r in results if r["summary"].get(m) is not None]
        best[m] = max(scores) if scores else -1

    for r in results:
        cells = [r["label"]]
        for m in metrics:
            v = r["summary"].get(m)
            s = _fmt_score(v)
            # 标记最优
            if v is not None and v == best.get(m) and v > 0:
                s = f"* {s}"
            cells.append(s)
        print("|" + "|".join(c.center(w) for c, w in zip(cells, col_w)) + "|")

    print(sep)
    print()

    # 差异分析：以 baseline 为参照
    baseline = results[0] if results else None
    if baseline and len(results) > 1:
        baseline_overall = baseline["summary"].get("overall_score")
        print("[vs baseline] 差异：")
        for r in results[1:]:
            diff = (r["summary"].get("overall_score") or 0) - (baseline_overall or 0)
            sign = "+" if diff >= 0 else ""
            print(f"  {r['label']}: overall {sign}{diff:.3f}")
        print()


def save_results(results: list[dict], eval_set_path: str) -> str:
    """保存完整结果到 JSON 文件，返回文件路径。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = f"eval_compare_{ts}.json"
    payload = {
        "generated_at": datetime.now().isoformat(),
        "eval_set_source": eval_set_path,
        "experiments": results,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    logger.info("完整结果已保存: %s", out_path)
    return out_path


# ──────────────────── 主流程 ────────────────────


async def run(args) -> None:
    # 加载评估集
    eval_set_path = Path(args.eval_set)
    if not eval_set_path.exists():
        raise FileNotFoundError(f"评估集文件不存在: {eval_set_path}")
    with open(eval_set_path, encoding="utf-8") as f:
        raw = json.load(f)
    # 支持两种格式：直接 list[QA] 或 {"eval_set": list[QA]}
    if isinstance(raw, list):
        eval_set = raw
    elif isinstance(raw, dict):
        eval_set = raw.get("eval_set") or raw.get("items") or []
    else:
        raise ValueError(f"评估集格式不支持: {type(raw)}")

    logger.info("评估集: %d 题 (%s)", len(eval_set), eval_set_path.name)

    # 确定实验组
    if args.experiments:
        exp_names = [e.strip() for e in args.experiments.split(",")]
    else:
        exp_names = list(EXPERIMENTS.keys())

    unknown = [n for n in exp_names if n not in EXPERIMENTS]
    if unknown:
        raise ValueError(
            f"未知实验名: {unknown}\n可选: {list(EXPERIMENTS.keys())}"
        )

    logger.info("实验组: %s", exp_names)

    results: list[dict] = []

    async with httpx.AsyncClient(
        base_url=BASE_URL, timeout=180, trust_env=False,
    ) as client:
        # 预检
        r = await client.get("/health")
        assert r.status_code == 200, "FastAPI 未启动"
        logger.info("FastAPI 健康 ✓")

        for i, exp_name in enumerate(exp_names):
            exp = EXPERIMENTS[exp_name]
            label = exp["label"]
            retrieval_options = exp["retrieval_options"]

            logger.info(
                "\n[%d/%d] ▶ %s (threshold=%s)",
                i + 1, len(exp_names), label,
                retrieval_options.get("similarity_threshold", "N/A"),
            )

            try:
                # 提交评估
                eval_task_id = await create_evaluation(
                    client, args.kb_id, eval_set, retrieval_options,
                    name=f"A1-compare-{exp_name}",
                )

                # 轮询等完成
                eval_result = await poll_evaluation(
                    client, args.kb_id, eval_task_id, args.poll_timeout,
                )

                summary = eval_result.get("summary") or {}
                logger.info(
                    "  ✓ 完成: overall=%s faith=%s ans_rel=%s ctx_prec=%s ctx_rec=%s",
                    _fmt_score(summary.get("overall_score")),
                    _fmt_score(summary.get("faithfulness")),
                    _fmt_score(summary.get("answer_relevancy")),
                    _fmt_score(summary.get("context_precision")),
                    _fmt_score(summary.get("context_recall")),
                )

                results.append({
                    "experiment": exp_name,
                    "label": label,
                    "eval_task_id": eval_task_id,
                    "retrieval_options": retrieval_options,
                    "summary": summary,
                    "details_count": len(eval_result.get("details") or []),
                })

            except Exception as e:
                logger.error("  ✗ 实验失败: %s err=%s", label, e)
                results.append({
                    "experiment": exp_name,
                    "label": label,
                    "eval_task_id": None,
                    "retrieval_options": retrieval_options,
                    "summary": {},
                    "details_count": 0,
                    "error": str(e),
                })

    # ── 输出对比报告 ──
    print_comparison_table(results)
    out_path = save_results(results, str(eval_set_path))
    logger.info("报告已输出: %s", out_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="RAG 调优评估对比脚本（A.1 Reranker 调优）"
    )
    parser.add_argument(
        "--kb-id", required=True,
        help="目标知识库 ID（UUID）",
    )
    parser.add_argument(
        "--eval-set", required=True,
        help="评估集 JSON 文件路径（格式同 POST /evaluate 的 eval_set 字段）",
    )
    parser.add_argument(
        "--experiments", default=None,
        help="逗号分隔的实验名（不传则跑全部预定义实验）。"
             f"可选: {list(EXPERIMENTS.keys())}",
    )
    parser.add_argument(
        "--poll-timeout", type=int, default=DEFAULT_POLL_TIMEOUT,
        help=f"每组实验的轮询超时秒数（默认 {DEFAULT_POLL_TIMEOUT}）",
    )
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
