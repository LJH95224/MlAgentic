"""V2.0 OBS-03 聚合统计快照写入。

每次 /v2/query 调用结束时，将关键指标汇总写一行到 query_analytics 表。
analytics 端点对该表做 SQL 聚合，无需扫描 agent_traces 的 JSONB 字段。

设计要点：
- 从 Tracer.steps 列表提取工具使用 bool / Token 数 / 步骤数 / 错误
- confidence 和 total_latency_ms 由调用方传入（已在 query.py 中计算好）
- 写入失败仅 warning，不阻断主链路
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.query_analytics import QueryAnalytics

logger = logging.getLogger(__name__)

# 低置信度阈值（与 confidence.py 保持一致）
_LOW_CONFIDENCE_THRESHOLD = 0.5


def build_analytics_snapshot(
    *,
    trace_id: str,
    session_id: uuid.UUID | None,
    kb_id: uuid.UUID | None,
    total_latency_ms: int | None,
    confidence: float | None,
    enable_faithfulness_check: bool,
    steps: list,
) -> dict:
    """从 trace 步骤列表构建 query_analytics 行数据。

    Args:
        trace_id: trace 唯一标识
        session_id: 关联会话 ID
        kb_id: 关联知识库 ID
        total_latency_ms: 总延迟
        confidence: CHC-03 置信度
        enable_faithfulness_check: 自检开关是否开启
        steps: Tracer.steps 列表（TraceStep 对象列表）

    Returns:
        dict，可直接传给 QueryAnalytics 构造函数
    """
    # 工具使用 bool
    graph_rag_triggered = False
    bm25_contributed = False
    faithfulness_check_triggered = False
    total_tokens = 0
    has_error = False

    for s in steps:
        # Graph RAG 触发：有 graph_anchor 步骤且有 tag 输出
        if s.step_type == "graph_anchor":
            output = s.step_output or {}
            if output.get("tag_count", 0) > 0:
                graph_rag_triggered = True

        # BM25 参与：有 retrieve 步骤即认为 BM25 可能贡献（默认 bm25_enable=True）
        if s.step_type == "retrieve":
            bm25_contributed = True

        # 答案自检触发
        if s.step_type == "faithfulness_check":
            faithfulness_check_triggered = True

        # Token 累加
        if s.token_count:
            total_tokens += s.token_count

        # 错误检测
        if s.error_message:
            has_error = True

    # 如果 faithfulness_check 未开启，标记为未触发
    if not enable_faithfulness_check:
        faithfulness_check_triggered = False

    # 低置信度
    low_confidence = confidence is not None and confidence < _LOW_CONFIDENCE_THRESHOLD

    return {
        "trace_id": trace_id,
        "session_id": session_id,
        "kb_id": kb_id,
        "total_latency_ms": total_latency_ms,
        "confidence": confidence,
        "low_confidence": low_confidence,
        "graph_rag_triggered": graph_rag_triggered,
        "bm25_contributed": bm25_contributed,
        "faithfulness_check_triggered": faithfulness_check_triggered,
        "total_tokens": total_tokens,
        "react_steps": len(steps),
        "has_error": has_error,
    }


async def write_analytics_snapshot(
    *,
    db: AsyncSession,
    trace_id: str,
    session_id: uuid.UUID | None,
    kb_id: uuid.UUID | None,
    total_latency_ms: int | None,
    confidence: float | None,
    enable_faithfulness_check: bool,
    steps: list,
) -> None:
    """构建并写入一行 query_analytics 快照。

    写入失败仅 warning，不阻断主链路。
    只 flush 不 commit，由调用方统一 commit。
    """
    try:
        data = build_analytics_snapshot(
            trace_id=trace_id,
            session_id=session_id,
            kb_id=kb_id,
            total_latency_ms=total_latency_ms,
            confidence=confidence,
            enable_faithfulness_check=enable_faithfulness_check,
            steps=steps,
        )
        row = QueryAnalytics(**data)
        db.add(row)
        await db.flush()
        # 注意：不 commit，由调用方统一 commit
    except Exception as e:
        logger.warning("Analytics 快照写入失败（已忽略）: %s", e)


__all__ = ["build_analytics_snapshot", "write_analytics_snapshot"]
