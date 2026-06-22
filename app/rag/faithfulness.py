"""V2.0 CHC-04 答案自检（Faithfulness Check）。

PRD §CHC-04：LLM 生成答案后，调一次轻量 LLM 把答案中的关键事实声明逐一比对
context，标 ``supported`` / ``unverified``，把 unverified 比例反馈给 confidence
当 ``hallucination_penalty``。

输出：

    [{"claim": "...", "status": "supported" | "unverified", "source_text": "..."}]

软失败原则：异常 / 超时 / JSON 解析失败 → status="skipped" + penalty=0.0，
不阻断主链路，不惩罚 confidence。

调用方：[app/api/v2/endpoints/query.py](../api/v2/endpoints/query.py) v2_query
在 citation_parse 之后插入。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

import litellm

from app.core.config import get_settings
from app.llm.client import build_completion_kwargs

logger = logging.getLogger(__name__)


# ──────────────────── Prompt ────────────────────

# 中文双引号避免 ASCII 闭合问题
FAITHFULNESS_SYSTEM_PROMPT = """给定上下文和答案，判断答案中每个关键事实声明是否有上下文支撑。

仅返回 JSON 数组，不要任何其他文字、不要 markdown 围栏：

[{"claim": "...", "status": "supported", "source_text": "..."}, ...]

约束：
- claim 是答案中的独立事实声明（数字、日期、定性论断、专有名词等）；闲谈、礼貌用语、总结性语言可忽略
- status 必须是 supported 或 unverified 二选一
  - supported：在上下文中有明确文本依据
  - unverified：在上下文中找不到明确支撑（可能是推断或幻觉）
- supported 时 source_text 给出上下文中的支撑句（不超过 50 字）；unverified 时填空字符串
- 答案中无可验证事实时，返回空数组 []"""


# JSON 解析时既支持模型直接返数组，也支持模型返 {"claims": [...]} 包装
_CLAIM_KEYS_TRY = ("claims", "results", "items", "data")


# ──────────────────── 数据类 ────────────────────


_FaithStatus = Literal["ok", "skipped", "disabled"]


@dataclass(frozen=True)
class FaithfulnessResult:
    """CHC-04 自检结果。

    - ``status="ok"``：自检跑通，claims/unverified/penalty 有效
    - ``status="skipped"``：自检异常或超时（PRD §586 风格），不影响主链路
    - ``status="disabled"``：开关关闭，未触发自检（与 ``skipped`` 区分便于排查）
    """

    status: _FaithStatus
    claims: list[dict] = field(default_factory=list)
    unverified: list[dict] = field(default_factory=list)
    hallucination_penalty: float = 0.0


# 模块级常量供主链路构造默认值（避免重复 new）
DISABLED_RESULT = FaithfulnessResult(status="disabled")
_SKIPPED_RESULT = FaithfulnessResult(status="skipped")


# ──────────────────── LLM 工具 ────────────────────


def _resolve_kwargs(messages: list[dict]) -> dict[str, Any]:
    """拼装自检 LLM 调用的 LiteLLM 参数。

    优先用 ``FAITHFULNESS_CHECK_MODEL``；缺省回退 ``LITELLM_MODEL``。
    厂商前缀推断与 [app/kg/ner.py](../kg/ner.py) 同款。
    """
    settings = get_settings()
    return build_completion_kwargs(
        messages=messages,
        model=settings.faithfulness_model,
        fallback_model=settings.litellm_model,
        required_model_label="FAITHFULNESS_CHECK_MODEL 或 LITELLM_MODEL",
        temperature=0.1,  # 自检要求确定性，低温度
        max_tokens=1500,
        num_retries=0,  # 自检失败软降级，不再多次重试增加延迟
        settings_obj=settings,
    )


def _strip_code_fence(text: str) -> str:
    """剥离 ```json ... ``` 围栏。"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _extract_content(resp: Any) -> str:
    """从 LiteLLM 响应中安全取出 message.content。"""
    if hasattr(resp, "model_dump"):
        resp = resp.model_dump()
    return resp["choices"][0]["message"]["content"] or ""


def _parse_claims(content: str) -> list[dict] | None:
    """解析 LLM JSON 输出 → claims 列表；失败返 None。

    支持两种格式：
    - 直接数组 ``[{...}, {...}]``
    - 包装对象 ``{"claims": [...]}``（部分模型在 response_format=json_object 时）
    """
    text = _strip_code_fence(content)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("CHC-04 JSON 解析失败：%s | 内容前 200 字: %r", e, content[:200])
        return None

    raw: Any
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict):
        raw = None
        for key in _CLAIM_KEYS_TRY:
            if isinstance(data.get(key), list):
                raw = data[key]
                break
        if raw is None:
            return None
    else:
        return None

    cleaned: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        claim = (item.get("claim") or "").strip()
        status = (item.get("status") or "").strip().lower()
        source_text = (item.get("source_text") or "").strip()
        if not claim:
            continue
        if status not in ("supported", "unverified"):
            continue
        cleaned.append(
            {"claim": claim, "status": status, "source_text": source_text}
        )
    return cleaned


# ──────────────────── 主入口 ────────────────────


async def check_faithfulness(
    *,
    answer: str,
    context: str,
) -> FaithfulnessResult:
    """LLM as Judge 答案自检。

    Args:
        answer: LLM 生成的答案文本（含 ``[N]`` 引用标记也无妨）
        context: build_context_with_citation 输出的检索上下文文本

    Returns:
        FaithfulnessResult；任何异常 / 超时 / 解析失败 → status="skipped" + penalty=0.0。
    """
    if not answer or not answer.strip() or not context.strip():
        return _SKIPPED_RESULT

    settings = get_settings()
    timeout = settings.faithfulness_check_timeout_s

    user_msg = f"上下文：\n{context}\n\n答案：\n{answer}"

    try:
        kwargs = _resolve_kwargs(
            messages=[
                {"role": "system", "content": FAITHFULNESS_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ]
        )
        # 部分模型支持 array 直接输出；不支持时靠 prompt 兜底
        # 这里不强制 response_format={"type":"json_object"}，因为 PRD 期望返回数组
        # （array 不在 json_object 范畴）。围栏剥离 + 包装格式兜底已能覆盖大多数情况。
        resp = await asyncio.wait_for(litellm.acompletion(**kwargs), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("CHC-04 答案自检超时 timeout=%.1fs", timeout)
        return _SKIPPED_RESULT
    except Exception as e:  # noqa: BLE001
        logger.warning("CHC-04 答案自检失败 err=%s: %s", type(e).__name__, e)
        return _SKIPPED_RESULT

    content = _extract_content(resp)
    claims = _parse_claims(content)
    if claims is None:
        return _SKIPPED_RESULT

    unverified = [c for c in claims if c["status"] == "unverified"]
    # penalty = unverified 比例；无 claims 时按 0（什么事实都没说，不视为幻觉）
    penalty = (len(unverified) / len(claims)) if claims else 0.0

    return FaithfulnessResult(
        status="ok",
        claims=claims,
        unverified=unverified,
        hallucination_penalty=round(penalty, 4),
    )


# ──────────────────── 答案文本增强 ────────────────────


def append_unverified_warning(answer: str, unverified: list[dict]) -> str:
    """如有 unverified 事实，在 answer 末尾追加警告清单；否则原样返回。

    格式（PRD §585 折中——追加文本清单方案）：

        <answer 原文>

        ⚠ 以下事实未在检索内容中找到明确支撑：
        - claim 1
        - claim 2
    """
    if not unverified:
        return answer

    lines = ["", "", "⚠ 以下事实未在检索内容中找到明确支撑："]
    for c in unverified:
        claim = (c.get("claim") or "").strip()
        if not claim:
            continue
        lines.append(f"- {claim}")
    return answer + "\n".join(lines)


__all__ = [
    "FaithfulnessResult",
    "FAITHFULNESS_SYSTEM_PROMPT",
    "DISABLED_RESULT",
    "check_faithfulness",
    "append_unverified_warning",
    "_parse_claims",  # 暴露给单测
]
