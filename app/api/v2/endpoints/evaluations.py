"""RAGAS 评估端点（EVA-01/02/03）。

端点：
- POST /api/v2/knowledge-bases/{kb_id}/evaluate            创建评估
- GET  /api/v2/knowledge-bases/{kb_id}/evaluations/{eid}   查进度+结果
- GET  /api/v2/knowledge-bases/{kb_id}/evaluations         评估历史列表

异步执行：POST 立即返回 eval_task_id，实际评估由 Celery worker 跑。
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import error_codes
from app.api.deps import get_db
from app.api.exceptions import BusinessError
from app.core.config import get_settings
from app.models.eval_task import EVAL_STATUS_PENDING, EvalTask
from app.schemas.v2.eval import (
    EvalCreateRequest,
    EvalCreateResponse,
    EvalDetailItem,
    EvalDetailResponse,
    EvalListItem,
    EvalListResponse,
    EvalSummary,
)
from app.services.kb_service import get_kb_or_raise

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge-bases/{kb_id}/evaluations", tags=["V2 RAGAS 评估"])

# 单独一个 router 给 POST /evaluate 路径（PRD 路径不带 's'），避免和列表/详情路由冲突
create_router = APIRouter(prefix="/knowledge-bases/{kb_id}", tags=["V2 RAGAS 评估"])


# ──────────────── EVA-01 创建评估 ────────────────


@create_router.post("/evaluate", response_model=EvalCreateResponse)
async def create_evaluation(
    kb_id: uuid.UUID,
    body: EvalCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> EvalCreateResponse:
    """EVA-01：创建评估任务并立即返回 eval_task_id（异步执行）。"""
    settings = get_settings()

    # 1. 校验 KB 存在
    await get_kb_or_raise(db, kb_id)

    # 2. 校验评估集
    n = len(body.eval_set)
    if n == 0:
        raise BusinessError(error_codes.EVAL_DATASET_EMPTY, "评估集为空")
    if n > settings.eval_max_questions:
        raise BusinessError(
            error_codes.EVAL_DATASET_TOO_LARGE,
            f"评估集题数 {n} 超出上限 {settings.eval_max_questions}",
        )

    # 3. 写 EvalTask 行
    eval_dataset = {
        "eval_set": [qa.model_dump() for qa in body.eval_set],
    }
    retrieval_options = body.retrieval_options.model_dump(exclude_none=True)
    eval_config = {
        "retrieval_options": retrieval_options,
        "kb_id": str(kb_id),
        "eval_llm_model": settings.eval_llm_model or settings.litellm_model,
        "embedding_model": settings.embedding_model,
    }

    eval_task = EvalTask(
        kb_id=kb_id,
        name=body.name or f"eval-{uuid.uuid4().hex[:8]}",
        status=EVAL_STATUS_PENDING,
        progress=0,
        eval_dataset=eval_dataset,
        eval_config=eval_config,
        question_count=n,
    )
    db.add(eval_task)
    await db.commit()
    await db.refresh(eval_task)

    # 4. 提交 Celery 任务
    # 延迟 import 避免循环依赖（celery_app → tasks/__init__ → models → 反向）
    from app.tasks.eval_task import run_evaluation_task

    try:
        run_evaluation_task.delay(str(eval_task.id))
    except Exception as e:  # noqa: BLE001
        # Celery 不可达 → 任务行还在，下次手工触发即可；但 API 层要告诉前端调度失败
        logger.error("eval_task.delay 失败 eval_task_id=%s err=%s", eval_task.id, e)
        raise BusinessError(
            error_codes.CELERY_UNAVAILABLE,
            f"评估任务调度失败：{type(e).__name__}",
        )

    logger.info(
        "EVA-01: 已创建评估任务 id=%s kb=%s n=%d",
        eval_task.id, kb_id, n,
    )
    return EvalCreateResponse(eval_task_id=eval_task.id, status=eval_task.status)


# ──────────────── EVA-02 查进度+结果 ────────────────


@router.get("/{eval_task_id}", response_model=EvalDetailResponse)
async def get_evaluation(
    kb_id: uuid.UUID,
    eval_task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> EvalDetailResponse:
    """EVA-02：查询评估进度 + 完成后的指标结果。"""
    await get_kb_or_raise(db, kb_id)

    row = (
        await db.execute(
            select(EvalTask).where(
                EvalTask.id == eval_task_id,
                EvalTask.kb_id == kb_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise BusinessError(
            error_codes.NOT_FOUND,
            f"评估任务 {eval_task_id} 在知识库 {kb_id} 下不存在",
        )

    return _row_to_detail(row)


# ──────────────── EVA-03 评估历史 ────────────────


@router.get("", response_model=EvalListResponse)
async def list_evaluations(
    kb_id: uuid.UUID,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: AsyncSession = Depends(get_db),
) -> EvalListResponse:
    """EVA-03：评估历史列表（按 created_at 倒序，分页）。"""
    await get_kb_or_raise(db, kb_id)

    # 总数
    count_result = await db.execute(
        select(func.count(EvalTask.id)).where(EvalTask.kb_id == kb_id)
    )
    total = count_result.scalar() or 0

    # 分页查询；按 (created_at desc, id desc) 双键排序避免 tie 不稳定
    offset = (page - 1) * page_size
    result = await db.execute(
        select(EvalTask)
        .where(EvalTask.kb_id == kb_id)
        .order_by(EvalTask.created_at.desc(), EvalTask.id.desc())
        .offset(offset)
        .limit(page_size)
    )
    rows = result.scalars().all()

    items = [
        EvalListItem(
            eval_task_id=r.id,
            name=r.name,
            status=r.status,
            progress=r.progress,
            question_count=r.question_count,
            summary=_extract_summary(r),
            retrieval_options=(r.eval_config or {}).get("retrieval_options"),
            created_at=r.created_at,
            completed_at=r.completed_at,
        )
        for r in rows
    ]

    return EvalListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# ──────────────── 内部辅助 ────────────────


def _extract_summary(row: EvalTask) -> EvalSummary | None:
    """从 eval_result JSONB 取 summary；未完成 / 缺失时返 None。"""
    if not row.eval_result:
        return None
    raw = row.eval_result.get("summary") if isinstance(row.eval_result, dict) else None
    if not raw or not isinstance(raw, dict):
        return None
    # 用 Pydantic 校验 [0, 1] 范围；越界字段直接置 None（评估软降级写入也可能落不规范值）
    safe: dict = {}
    for k in ("faithfulness", "answer_relevancy", "context_precision",
              "context_recall", "overall_score"):
        v = raw.get(k)
        if v is None:
            safe[k] = None
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            safe[k] = None
            continue
        safe[k] = f if 0.0 <= f <= 1.0 else None
    return EvalSummary(**safe)


def _extract_details(row: EvalTask) -> list[EvalDetailItem] | None:
    """从 eval_result JSONB 取 details；未完成 / 缺失时返 None。"""
    if not row.eval_result:
        return None
    raw = row.eval_result.get("details") if isinstance(row.eval_result, dict) else None
    if not raw or not isinstance(raw, list):
        return None
    items: list[EvalDetailItem] = []
    for d in raw:
        if not isinstance(d, dict):
            continue
        try:
            items.append(EvalDetailItem(**d))
        except Exception as e:  # noqa: BLE001
            logger.warning("EVA-02: details 单条校验失败（已跳过）err=%s d=%r", e, d)
    return items or None


def _row_to_detail(row: EvalTask) -> EvalDetailResponse:
    """EvalTask ORM 行 → EvalDetailResponse。"""
    return EvalDetailResponse(
        eval_task_id=row.id,
        kb_id=row.kb_id,
        name=row.name,
        status=row.status,
        progress=row.progress,
        question_count=row.question_count,
        summary=_extract_summary(row),
        details=_extract_details(row),
        retrieval_options=(row.eval_config or {}).get("retrieval_options"),
        error_message=row.error_message,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )
