"""V2.0 CHC-03 置信度评分。

PRD §CHC-03 公式：

    confidence = weighted_avg(rerank_scores of cited chunks)
               × coverage_factor
               × (1 − hallucination_penalty)

其中：
- ``weighted_avg(rerank_scores)``：被引用 chunk 的 Reranker 分数均值（这里所有
  被引用 chunk 等权，简单算术平均）
- ``coverage_factor``：``len(cited) / top_k``，被引用 chunk 占初筛的比例（上限 1.0）
- ``hallucination_penalty``：CHC-04 自检失败的事实比例（默认 0.0；自检关闭/失败时不惩罚）

PRD §553：``confidence < 0.5`` 时填 ``low_confidence_warning`` 文本预警。

纯函数无 IO，调用方：[app/api/v2/endpoints/query.py](../api/v2/endpoints/query.py) v2_query 末尾。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# PRD §556 原文文案（中文双引号避免 ASCII 闭合）
LOW_CONFIDENCE_THRESHOLD = 0.5
LOW_CONFIDENCE_WARNING_TEMPLATE = (
    "本次回答的文档依据不充分（置信度 {confidence:.2f}），"
    "建议人工核查或补充相关文档后重新查询。"
)

# 极小数值视为 0，避免浮点噪音让前端展示成 1e-9
_EPSILON = 1e-9


@dataclass(frozen=True)
class ConfidenceScore:
    """CHC-03 置信度评分结果。

    ``breakdown`` 透出三因子原值（weighted_score / coverage / penalty），
    便于 trace 排查"为什么这次评分这么低"。
    """

    confidence: float
    low_confidence_warning: str | None
    breakdown: dict = field(default_factory=dict)


def _safe_score(c: dict) -> float:
    """从 cited chunk dict 取 rerank_score；None / 非数字按 0 计。"""
    s = c.get("rerank_score")
    if s is None:
        return 0.0
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def compute_confidence(
    *,
    cited_chunks: list[dict],
    top_k: int,
    hallucination_penalty: float = 0.0,
) -> ConfidenceScore:
    """根据 PRD §540 公式算 confidence + 触发预警。

    Args:
        cited_chunks: ``parse_citations`` 的输出列表（已去重，仅含被引用的 chunk）
        top_k: ``ResolvedRetrievalOptions.top_k``，作为 coverage 的分母
        hallucination_penalty: CHC-04 自检的不忠实事实比例 [0, 1]；自检关闭/失败时为 0.0

    Returns:
        ConfidenceScore；空 cited_chunks 时 confidence=0.0 + 触发警告。
    """
    # 检索为空 / 全部未被引用 → confidence=0
    if not cited_chunks:
        return ConfidenceScore(
            confidence=0.0,
            low_confidence_warning=LOW_CONFIDENCE_WARNING_TEMPLATE.format(confidence=0.0),
            breakdown={"weighted_score": 0.0, "coverage": 0.0, "penalty": 0.0},
        )

    weighted_score = sum(_safe_score(c) for c in cited_chunks) / len(cited_chunks)
    # coverage 上限 1.0；top_k 异常（<=0）时退化为 1.0 不放大
    coverage = 1.0
    if top_k > 0:
        coverage = min(len(cited_chunks) / top_k, 1.0)

    # penalty 夹值到 [0, 1]
    penalty = max(0.0, min(float(hallucination_penalty), 1.0))

    raw = weighted_score * coverage * (1.0 - penalty)
    if raw < _EPSILON:
        confidence = 0.0
    elif raw > 1.0:
        # 理论上不会超 1（rerank 来自 [0, 1] + coverage 已夹值），兜底
        confidence = 1.0
    else:
        confidence = round(raw, 4)

    warning: str | None = None
    if confidence < LOW_CONFIDENCE_THRESHOLD:
        warning = LOW_CONFIDENCE_WARNING_TEMPLATE.format(confidence=confidence)

    return ConfidenceScore(
        confidence=confidence,
        low_confidence_warning=warning,
        breakdown={
            "weighted_score": round(weighted_score, 4),
            "coverage": round(coverage, 4),
            "penalty": round(penalty, 4),
        },
    )


__all__ = [
    "ConfidenceScore",
    "compute_confidence",
    "LOW_CONFIDENCE_THRESHOLD",
    "LOW_CONFIDENCE_WARNING_TEMPLATE",
]
