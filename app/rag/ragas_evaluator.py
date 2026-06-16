"""RAGAS 4 项指标评估管道（V2.0 EVA-02，T11 阶段）。

使用 ragas 0.2+ 官方库的 `evaluate()` API 计算 4 项核心指标：
- faithfulness：答案事实是否有文档支撑
- answer_relevancy：答案是否真正回答了问题
- context_precision：检索 chunk 的有用占比
- context_recall：标准答案信息是否被召回

LLM/Embedding 适配 LiteLLM：通过 LangChain 的 ChatOpenAI / OpenAIEmbeddings
配 `base_url` 走 OpenAI 兼容协议，再用 ragas 的 LangchainLLMWrapper / LangchainEmbeddingsWrapper 包一层。

设计要点：
- 整批 evaluate 调用包 try/except，失败时整批指标返 None（不阻断 EvalTask 落 result）
- 单题 ragas 失败用 `raise_exceptions=False`，对应行返 NaN，转 None 输出
- `overall_score` = 4 项算术均值（PRD §853）；任一指标为 None 时仍尝试用其余项算均值
- 模块导入 ragas 推迟到函数内（懒加载），避免单测/环境无 ragas 时模块级 import 失败
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)


# 4 项核心指标的字段名（PRD §817）
METRIC_FIELDS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")


def _to_float_or_none(v: Any) -> float | None:
    """ragas 单题分数：成功是 float，失败是 NaN，转成 None 便于 JSONB 序列化。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _compute_overall(metrics: dict[str, float | None]) -> float | None:
    """整批 summary 的 overall_score = 4 项算术均值；全 None 时返 None。"""
    valid = [v for v in metrics.values() if v is not None]
    if not valid:
        return None
    return sum(valid) / len(valid)


def _build_evaluator_llm(model: str, api_key: str | None, api_base: str | None):
    """构造 ragas 用的 evaluator LLM（LiteLLM 兼容协议走 ChatOpenAI）。"""
    from langchain_openai import ChatOpenAI
    from ragas.llms import LangchainLLMWrapper

    # 厂商前缀对 LangChain ChatOpenAI 不需要（与 litellm 不同）；剥掉模型名前缀
    raw_model = model.split("/", 1)[1] if "/" in model else model

    chat = ChatOpenAI(
        model=raw_model,
        api_key=api_key or "",
        base_url=api_base or None,
        temperature=0.0,  # 评估期追求确定性
    )
    return LangchainLLMWrapper(chat)


def _build_evaluator_embeddings(model: str, api_key: str | None, api_base: str | None):
    """构造 ragas 用的 embeddings（answer_relevancy/context_precision 必需）。"""
    from langchain_openai import OpenAIEmbeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper

    raw_model = model.split("/", 1)[1] if "/" in model else model

    emb = OpenAIEmbeddings(
        model=raw_model,
        api_key=api_key or "",
        base_url=api_base or None,
    )
    return LangchainEmbeddingsWrapper(emb)


async def evaluate_with_ragas(
    samples: list[dict[str, Any]],
    *,
    llm_model: str,
    llm_api_key: str | None = None,
    llm_api_base: str | None = None,
    embedding_model: str | None = None,
    embedding_api_key: str | None = None,
    embedding_api_base: str | None = None,
) -> dict[str, Any]:
    """跑 RAGAS 评估。

    Args:
        samples: [{"question", "ground_truth", "answer", "contexts"}, ...]
            contexts 是 list[str]，是 chunk 的原文文本列表
        llm_model: 评估期 LLM-as-Judge 用的模型（建议轻量模型，如 deepseek/deepseek-v4-flash）
        llm_api_key/api_base: LiteLLM 兼容的 chat 端点配置
        embedding_model: embedding 模型（context_precision/recall 需要）
        embedding_api_key/api_base: embedding 端点配置

    Returns:
        {
            "summary": {faithfulness, answer_relevancy, context_precision, context_recall, overall_score},
            "details": [{question, ..., faithfulness, ...}],
            "metric_backend": "ragas-0.2",
            "error": str | None
        }

        整批失败（ragas import / 调用失败）→ summary 各项 None；details 仍按 samples 长度返回
        单题失败 → 该题对应指标 None，整批仍可成功
    """
    n = len(samples)
    if n == 0:
        return {
            "summary": {f: None for f in METRIC_FIELDS} | {"overall_score": None},
            "details": [],
            "metric_backend": "ragas-0.2",
            "error": "samples is empty",
        }

    # 默认空 details（即使整批失败也要按 samples 透出每条 question 等）
    default_details = [
        {
            "question": s.get("question", ""),
            "ground_truth": s.get("ground_truth", ""),
            "answer": s.get("answer", ""),
            "contexts": s.get("contexts") or [],
            "faithfulness": None,
            "answer_relevancy": None,
            "context_precision": None,
            "context_recall": None,
            "error": s.get("error"),
        }
        for s in samples
    ]
    empty_summary = {f: None for f in METRIC_FIELDS} | {"overall_score": None}

    # 懒加载 ragas，环境缺包时整批软降级
    try:
        # ── ragas 0.4.x + langchain-community 1.x 兼容 shim ──
        # ragas 在 ragas/llms/base.py 顶部 import `langchain_community.chat_models.vertexai`，
        # 但 langchain-community 1.x 已把 vertexai 拆成独立 langchain-google-vertexai 包。
        # 项目不用 vertexai，注入一个 stub 模块让 ragas 顶层 import 通过即可。
        import sys
        import types as _types

        if "langchain_community.chat_models.vertexai" not in sys.modules:
            _stub = _types.ModuleType("langchain_community.chat_models.vertexai")

            class _ChatVertexAIStub:
                """ragas 模块顶层 import 的占位；不会被实际实例化（项目不调 vertex）。"""

            _stub.ChatVertexAI = _ChatVertexAIStub
            sys.modules["langchain_community.chat_models.vertexai"] = _stub

        from ragas import evaluate
        from ragas.dataset_schema import EvaluationDataset, SingleTurnSample

        # ragas 0.4 起从 ragas.metrics 直接 import 会报 DeprecationWarning，
        # 推荐路径是 ragas.metrics.collections；但 collections 下的指标用 async ascore 接口
        # 与 evaluate() 函数式调用兼容性差。0.4.x 仍可走传统路径，0.5+ 再迁移。
        from ragas.metrics import (
            AnswerRelevancy,
            ContextPrecision,
            ContextRecall,
            Faithfulness,
        )
    except ImportError as e:
        logger.error("ragas 库未安装或版本不兼容（需 ragas>=0.2.0）：%s", e)
        return {
            "summary": empty_summary,
            "details": default_details,
            "metric_backend": "ragas-0.2",
            "error": f"ragas import failed: {e}",
        }

    # 构造 LLM/Embedding wrapper（任一失败整批降级）
    try:
        evaluator_llm = _build_evaluator_llm(llm_model, llm_api_key, llm_api_base)
        if not embedding_model:
            raise ValueError("embedding_model 未配置（context_precision / context_recall 必需）")
        evaluator_emb = _build_evaluator_embeddings(
            embedding_model, embedding_api_key, embedding_api_base
        )
    except Exception as e:  # noqa: BLE001
        logger.error("ragas evaluator 初始化失败：%s", e, exc_info=True)
        return {
            "summary": empty_summary,
            "details": default_details,
            "metric_backend": "ragas-0.2",
            "error": f"evaluator init failed: {type(e).__name__}: {e}",
        }

    # 构造 ragas 数据集
    ragas_samples = [
        SingleTurnSample(
            user_input=s.get("question", ""),
            retrieved_contexts=list(s.get("contexts") or []),
            response=s.get("answer", "") or "",
            reference=s.get("ground_truth", ""),
        )
        for s in samples
    ]
    ds = EvaluationDataset(samples=ragas_samples)

    # 4 项指标
    metrics = [
        Faithfulness(llm=evaluator_llm),
        AnswerRelevancy(llm=evaluator_llm, embeddings=evaluator_emb),
        ContextPrecision(llm=evaluator_llm),
        ContextRecall(llm=evaluator_llm),
    ]

    # 跑评估；ragas 内部对单题失败用 raise_exceptions=False 转 NaN
    try:
        result = evaluate(
            dataset=ds,
            metrics=metrics,
            llm=evaluator_llm,
            embeddings=evaluator_emb,
            raise_exceptions=False,
            show_progress=False,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("ragas evaluate 整批失败：%s", e, exc_info=True)
        return {
            "summary": empty_summary,
            "details": default_details,
            "metric_backend": "ragas-0.2",
            "error": f"evaluate failed: {type(e).__name__}: {e}",
        }

    # 解析结果：result.scores 是 list[dict]，每条对应一个 sample
    raw_scores = list(getattr(result, "scores", []) or [])
    details = []
    summary_buckets: dict[str, list[float]] = {f: [] for f in METRIC_FIELDS}

    for i, s in enumerate(samples):
        row = raw_scores[i] if i < len(raw_scores) else {}
        # ragas 各指标返回的 key 与常用 snake_case 对齐：faithfulness / answer_relevancy
        # / context_precision / context_recall（与 PRD §817 完全一致）
        metric_values: dict[str, float | None] = {}
        for f in METRIC_FIELDS:
            v = _to_float_or_none(row.get(f) if isinstance(row, dict) else None)
            metric_values[f] = v
            if v is not None:
                summary_buckets[f].append(v)

        details.append(
            {
                "question": s.get("question", ""),
                "ground_truth": s.get("ground_truth", ""),
                "answer": s.get("answer", ""),
                "contexts": s.get("contexts") or [],
                **metric_values,
                "error": s.get("error"),
            }
        )

    summary = {
        f: (sum(vals) / len(vals)) if vals else None
        for f, vals in summary_buckets.items()
    }
    summary["overall_score"] = _compute_overall(summary)

    return {
        "summary": summary,
        "details": details,
        "metric_backend": "ragas-0.2",
        "error": None,
    }


__all__ = [
    "evaluate_with_ragas",
    "METRIC_FIELDS",
    "_compute_overall",
    "_to_float_or_none",
]
