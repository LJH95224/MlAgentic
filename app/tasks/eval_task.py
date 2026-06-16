"""RAGAS 评估异步任务（V2.0 EVA-01/02，T11 阶段）。

【架构约定】（与 session_task / ingest_task 一致）：
- @celery_app.task 同步壳；核心 async def _main；体内只 asyncio.run 一次
- PG 连接走 task_resources（每任务现建现断 NullPool）；评估不写 Milvus/Neo4j 但
  hybrid_search 里会用到全局 milvus_client（搜索期 fork-safe，task_resources 已起）

【主流程】（_run_evaluation_main）：
1. 加载 EvalTask；status → processing
2. 遍历 eval_dataset 每题：
   - 调 eval_runner.run_single_query_for_eval 拿 answer + contexts
   - 累计到 samples 列表（包含 question/ground_truth/answer/contexts/error）
   - 每完成 1 题更新 progress = 5 + int(85 * i / N)
3. 跑 ragas_evaluator.evaluate_with_ragas 整批打分（progress=90 → 95）
4. 写 eval_result(JSONB) + status=completed + progress=100 + completed_at=now
5. 任一不可恢复异常 → status=failed + error_message + traceback

【为何串行而非并发】：
- 评估期不追求时延，串行 + 限题数 100 + LLM 单题超时是双重保护（PRD §1147 风险表）
- ragas 内部已经按需并发；评估侧自己再并发会引入限流问题
"""

from __future__ import annotations

import asyncio
import logging
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update

from app.core.config import get_settings
from app.models.eval_task import (
    EVAL_STATUS_COMPLETED,
    EVAL_STATUS_FAILED,
    EVAL_STATUS_PROCESSING,
    EvalTask,
)
from app.tasks._resources import task_resources
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


# ──────────────── 辅助：进度写回 ────────────────


async def _update_progress(
    resources: Any,
    eval_task_id: uuid.UUID,
    *,
    progress: int,
    status: str | None = None,
) -> None:
    """安全更新 progress / status；失败仅记日志不抛错。

    用 resources.db() 拿短 session（与其他步骤一致，避免持有过久）。
    """
    try:
        async with resources.db() as session:
            values: dict[str, Any] = {"progress": progress}
            if status is not None:
                values["status"] = status
            await session.execute(
                update(EvalTask).where(EvalTask.id == eval_task_id).values(**values)
            )
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("eval_task: 进度写回失败 id=%s err=%s", eval_task_id, e)


def _resolve_eval_llm_kwargs(model_override: str | None) -> dict[str, Any]:
    """挑选评估期 LLM 模型 + key/base；与 session_task._resolve_kwargs 同款风格。

    Returns:
        {"model": ..., "api_key": ..., "api_base": ...}
    """
    settings = get_settings()
    model = model_override or settings.litellm_model
    if not model:
        raise ValueError(
            "EVAL_LLM_MODEL / LITELLM_MODEL 都未配置，无法运行 RAGAS 评估"
        )

    # 厂商前缀自动补全（与 session_task 一致）
    if "/" not in model and settings.litellm_api_base:
        if "deepseek.com" in settings.litellm_api_base:
            model = f"deepseek/{model}"
        elif "dashscope.aliyuncs.com" in settings.litellm_api_base:
            model = f"dashscope/{model}"
        elif "open.bigmodel.cn" in settings.litellm_api_base:
            model = f"zhipu/{model}"

    return {
        "model": model,
        "api_key": settings.litellm_api_key,
        "api_base": settings.litellm_api_base,
    }


# ──────────────── 主流程 ────────────────


async def _run_evaluation_main(eval_task_id_str: str) -> dict[str, Any]:
    """评估任务主流程。"""
    settings = get_settings()
    eval_task_id = uuid.UUID(eval_task_id_str)

    # ── 全局单例预热：worker 进程没跑 FastAPI lifespan，
    # 全局 milvus_client 默认未初始化；hybrid_search 直接调用会报
    # "Milvus 客户端尚未初始化"。在此显式调一次 init_milvus（幂等：已 init 直接返回单例）。
    # 与 ingest_task / session_task 不同，eval_task 走的是查询链路（hybrid_search），
    # 它依赖 app.rag.milvus_client 的全局 _client 而非 task_resources 提供的临时 client。
    try:
        from app.rag.milvus_client import init_milvus

        init_milvus()
    except Exception as e:  # noqa: BLE001
        logger.error("eval_task: Milvus 初始化失败，评估将中止：%s", e, exc_info=True)
        # 提前标 failed，避免后面所有题都报 Milvus 未初始化
        try:
            async with task_resources() as _res:
                async with _res.db() as session:
                    await session.execute(
                        update(EvalTask)
                        .where(EvalTask.id == eval_task_id)
                        .values(
                            status=EVAL_STATUS_FAILED,
                            error_message=f"Milvus init failed: {type(e).__name__}: {e}"[:4000],
                            completed_at=datetime.now(timezone.utc),
                        )
                    )
                    await session.commit()
        except Exception as e2:  # noqa: BLE001
            logger.error("eval_task: 标记 failed 也失败 err=%s", e2)
        return {"status": "failed", "reason": f"milvus_init: {e}"}

    async with task_resources() as resources:
        # ── Step 1: 加载 EvalTask + 标记 processing ──
        async with resources.db() as session:
            row = (
                await session.execute(
                    select(EvalTask).where(EvalTask.id == eval_task_id)
                )
            ).scalar_one_or_none()
            if row is None:
                logger.warning("eval_task: id=%s 不存在", eval_task_id)
                return {"status": "skipped", "reason": "eval_task_not_found"}

            kb_id = row.kb_id
            eval_dataset = row.eval_dataset or {}
            eval_config = row.eval_config or {}
            question_count = row.question_count

            await session.execute(
                update(EvalTask)
                .where(EvalTask.id == eval_task_id)
                .values(status=EVAL_STATUS_PROCESSING, progress=5)
            )
            await session.commit()

        logger.info(
            "eval_task: 开始 id=%s kb=%s questions=%d",
            eval_task_id,
            kb_id,
            question_count,
        )

        # ── Step 2: 遍历每题跑 RAG ──
        # 延迟 import：避免循环依赖（eval_runner → query → tracer 等）
        from app.rag.eval_runner import run_single_query_for_eval

        qa_items = eval_dataset.get("eval_set") or []
        retrieval_options = eval_config.get("retrieval_options") or {}

        samples: list[dict[str, Any]] = []
        n = max(1, len(qa_items))

        for i, qa in enumerate(qa_items):
            question = (qa or {}).get("question", "")
            ground_truth = (qa or {}).get("ground_truth", "")
            sample: dict[str, Any] = {
                "question": question,
                "ground_truth": ground_truth,
                "answer": "",
                "contexts": [],
                "error": None,
            }

            try:
                async with resources.db() as session:
                    rag_result = await asyncio.wait_for(
                        run_single_query_for_eval(
                            query=question,
                            kb_ids=[kb_id],
                            options=retrieval_options,
                            db=session,
                        ),
                        timeout=settings.eval_question_timeout_s,
                    )
                sample["answer"] = rag_result.get("answer", "") or ""
                sample["contexts"] = rag_result.get("contexts") or []
                if rag_result.get("error"):
                    sample["error"] = rag_result["error"]
            except asyncio.TimeoutError:
                logger.warning(
                    "eval_task: 第 %d 题超时（%.0fs）question=%r",
                    i, settings.eval_question_timeout_s, question[:60],
                )
                sample["answer"] = "（生成失败：超时）"
                sample["error"] = f"timeout {settings.eval_question_timeout_s}s"
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "eval_task: 第 %d 题异常 question=%r err=%s",
                    i, question[:60], e, exc_info=True,
                )
                sample["answer"] = "（生成失败：异常）"
                sample["error"] = f"{type(e).__name__}: {e}"

            samples.append(sample)

            # 进度回写：5 → 90 区间留 90 → 95 给 ragas
            new_progress = 5 + int(85 * (i + 1) / n)
            # 每题都写一次，question_count 一般 ≤ 100，写盘成本可接受
            await _update_progress(
                resources, eval_task_id, progress=new_progress
            )

        # ── Step 3: ragas 整批打分 ──
        await _update_progress(
            resources, eval_task_id, progress=90
        )

        from app.rag.ragas_evaluator import evaluate_with_ragas

        try:
            llm_kwargs = _resolve_eval_llm_kwargs(settings.eval_llm_model)
        except ValueError as e:
            logger.error("eval_task: LLM 配置缺失 err=%s", e)
            await _update_progress(
                resources,
                eval_task_id,
                progress=90,
                status=EVAL_STATUS_FAILED,
            )
            async with resources.db() as session:
                await session.execute(
                    update(EvalTask)
                    .where(EvalTask.id == eval_task_id)
                    .values(error_message=str(e))
                )
                await session.commit()
            return {"status": "failed", "reason": str(e)}

        ragas_result = await evaluate_with_ragas(
            samples=samples,
            llm_model=llm_kwargs["model"],
            llm_api_key=llm_kwargs["api_key"],
            llm_api_base=llm_kwargs["api_base"],
            embedding_model=settings.embedding_model,
            embedding_api_key=settings.embedding_api_key,
            embedding_api_base=settings.embedding_api_base,
        )

        await _update_progress(
            resources, eval_task_id, progress=95
        )

        # ── Step 4: 写最终结果 ──
        # 评估失败但不抛：summary 各项可能 None，仍按 completed 落库（前端按 None 展示）
        final_status = EVAL_STATUS_COMPLETED
        error_message = ragas_result.get("error")

        # eval_result 完整存：summary + details + 评估期生成的 samples
        # （samples 与 details 字段重叠但保留前者便于复跑指标 / 调试）
        eval_result_payload = {
            "summary": ragas_result.get("summary") or {},
            "details": ragas_result.get("details") or [],
            "metric_backend": ragas_result.get("metric_backend"),
            "samples": samples,  # 原始 RAG 输出，用于复算
        }

        async with resources.db() as session:
            await session.execute(
                update(EvalTask)
                .where(EvalTask.id == eval_task_id)
                .values(
                    status=final_status,
                    progress=100,
                    eval_result=eval_result_payload,
                    error_message=error_message,
                    completed_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        logger.info(
            "eval_task: 完成 id=%s status=%s overall=%s",
            eval_task_id,
            final_status,
            (ragas_result.get("summary") or {}).get("overall_score"),
        )
        return {
            "status": "completed",
            "eval_task_id": str(eval_task_id),
            "summary": ragas_result.get("summary"),
        }


# ──────────────── Celery 任务入口（同步壳） ────────────────


@celery_app.task(name="app.tasks.eval_task.run_evaluation_task")
def run_evaluation_task(eval_task_id: str) -> dict[str, Any]:
    """EVA-01/02：异步跑 RAGAS 评估。

    任务由 POST /api/v2/knowledge-bases/{kb_id}/evaluate 调度。
    """
    logger.info("[eval] task 开始 eval_task_id=%s", eval_task_id)
    try:
        return asyncio.run(_run_evaluation_main(eval_task_id))
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc(limit=10)
        logger.error("[eval] task 失败 eval_task_id=%s err=%s", eval_task_id, exc)
        # 把 failed 状态尽量写回（用同步引擎避免再起 task_resources）
        try:
            asyncio.run(_mark_failed_safe(eval_task_id, str(exc), tb))
        except Exception as e2:  # noqa: BLE001
            logger.error("[eval] 标记 failed 失败 err=%s", e2)
        return {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": tb,
        }


async def _mark_failed_safe(eval_task_id: str, err: str, tb: str) -> None:
    """评估任务异常兜底：标 failed + error_message。"""
    sid = uuid.UUID(eval_task_id)
    async with task_resources() as resources:
        async with resources.db() as session:
            await session.execute(
                update(EvalTask)
                .where(EvalTask.id == sid)
                .values(
                    status=EVAL_STATUS_FAILED,
                    error_message=f"{err}\n{tb}"[:4000],
                    completed_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()


__all__ = [
    "run_evaluation_task",
    "_run_evaluation_main",
    "_resolve_eval_llm_kwargs",
]
