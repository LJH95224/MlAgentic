"""V2.0 Query 改写器（HRE-01）。

PRD §HRE-01 描述：用户原始 Query 往往简短模糊，直接做向量检索效果有限。改写后能提升召回多样性。

三种策略：
- ``none``：直接用原 query，零 LLM 调用
- ``hyde``：LLM 生成 100~200 字"假设性答案"，用其向量替代 Query 向量做检索
- ``multi_query``：LLM 一次生成 N 个不同角度的子查询，每路独立检索后 RRF 融合

软失败原则（与 [app/kg/ner.py](../kg/ner.py) 同款）：LLM 限流 / 超时 / JSON 解析失败时
返回"等价于 none"的结果，不阻断主链路；记 warning 让运维/trace 可见。

调用方：[app/api/v2/endpoints/query.py](../api/v2/endpoints/query.py) 的 v2_query。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import litellm

from app.core.config import get_settings
from app.llm.client import build_completion_kwargs

logger = logging.getLogger(__name__)


# ──────────────────── Prompt 定义 ────────────────────

# HyDE: Hypothetical Document Embeddings —— 让 LLM 写一段"假设性答案"做检索代理
HYDE_SYSTEM_PROMPT = """你是一个文档假设撰写助手。用户会给你一个问题，请用 100~200 字写出
一段假设性的理想答案（不需要真实，只需写得像「如果文档里有答案，它大概长什么样」）。

约束：
- 直接输出答案文本，不要任何前缀（如"答："）、引言、解释、markdown 围栏
- 用陈述句，不要反问；不要说"我不知道"或"需要更多信息"
- 行文尽量贴近正式文档语气，便于向量检索匹配真实文档"""


MULTI_QUERY_SYSTEM_PROMPT = """你是一个查询扩展助手。用户会给你一个原始问题，请从不同角度
改写出 {n} 个独立子查询，覆盖该问题可能涉及的不同方面。

仅返回 JSON 对象，格式严格如下，不要任何其他文字：

{{"sub_queries": ["子查询1", "子查询2", "子查询3"]}}

约束：
- sub_queries 数量必须正好 {n} 个
- 每个子查询独立成立，不依赖其他子查询的上下文
- 不要简单同义词替换；要从 不同维度 / 不同细节 / 不同上位概念 切入
- 子查询保持中文（除非原文是英文）"""


# ──────────────────── 数据类 ────────────────────


@dataclass(frozen=True)
class RewriteResult:
    """Query 改写结果。

    - none：rewritten_text=None, sub_queries=[]
    - hyde：rewritten_text=<假设答案>, sub_queries=[]
    - multi_query：rewritten_text=None, sub_queries=[q1, q2, q3]
    """

    rewritten_text: str | None = None
    sub_queries: list[str] = field(default_factory=list)


_NOOP_RESULT = RewriteResult()


# ──────────────────── LLM 调用工具 ────────────────────


def _resolve_rewriter_kwargs(messages: list[dict]) -> dict[str, Any]:
    """拼装改写器调用 LiteLLM 的参数。

    优先用 QUERY_REWRITER_MODEL；缺省则复用 LITELLM_MODEL（与 chat 同源）。
    厂商前缀推断逻辑与 [app/kg/ner.py](../kg/ner.py) 保持一致。
    """
    settings = get_settings()
    return build_completion_kwargs(
        messages=messages,
        model=settings.query_rewriter_model,
        fallback_model=settings.litellm_model,
        required_model_label="QUERY_REWRITER_MODEL 或 LITELLM_MODEL",
        temperature=0.3,
        settings_obj=settings,
    )


def _extract_content(resp: Any) -> str:
    """从 LiteLLM 返回中安全取出 message.content。

    兼容 Pydantic 对象与裸 dict（与 [app/kg/ner.py](../kg/ner.py) 同款）。
    """
    if hasattr(resp, "model_dump"):
        resp = resp.model_dump()
    return resp["choices"][0]["message"]["content"] or ""


def _strip_code_fence(text: str) -> str:
    """剥离 ```json ... ``` 围栏（LLM 偶尔会加）。"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


# ──────────────────── 子策略实现 ────────────────────


async def _do_hyde(query: str) -> RewriteResult:
    """生成假设答案。"""
    kwargs = _resolve_rewriter_kwargs(
        messages=[
            {"role": "system", "content": HYDE_SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ]
    )
    # max_tokens 留给 hyde 一段假设答案足够（200 字中文约 300 token）
    kwargs["max_tokens"] = 500

    resp = await litellm.acompletion(**kwargs)
    content = _extract_content(resp).strip()

    # 假设答案为空或过短（< 10 字）视为退化，软降级为 none
    if len(content) < 10:
        logger.warning("HyDE 生成内容过短，降级为原 query 检索：%r", content[:80])
        return _NOOP_RESULT

    return RewriteResult(rewritten_text=content, sub_queries=[])


async def _do_multi_query(query: str, n_sub: int) -> RewriteResult:
    """生成 N 个子查询。"""
    kwargs = _resolve_rewriter_kwargs(
        messages=[
            {
                "role": "system",
                "content": MULTI_QUERY_SYSTEM_PROMPT.format(n=n_sub),
            },
            {"role": "user", "content": query},
        ]
    )
    kwargs["response_format"] = {"type": "json_object"}
    kwargs["max_tokens"] = 800

    resp = await litellm.acompletion(**kwargs)
    content = _extract_content(resp)

    parsed = json.loads(_strip_code_fence(content))
    raw = parsed.get("sub_queries") or []

    sub_queries: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        if cleaned:
            sub_queries.append(cleaned)

    if not sub_queries:
        logger.warning("multi_query 生成的子查询全空，降级为原 query 检索")
        return _NOOP_RESULT

    # 上限不超过请求的 n_sub；下限若 < 2 也保留（最坏退化为单路检索）
    sub_queries = sub_queries[:n_sub]
    return RewriteResult(rewritten_text=None, sub_queries=sub_queries)


# ──────────────────── 主入口 ────────────────────


async def rewrite_query(
    query: str,
    strategy: str,
    *,
    n_sub: int | None = None,
) -> RewriteResult:
    """根据策略改写查询。

    Args:
        query: 用户原始查询
        strategy: "none" / "hyde" / "multi_query"
        n_sub: multi_query 子查询数量；None 时取 settings.multi_query_count

    Returns:
        RewriteResult；任何异常/超时都软降级返 _NOOP_RESULT，**不抛错**。
    """
    if not query or not query.strip():
        return _NOOP_RESULT

    if strategy == "none":
        return _NOOP_RESULT

    settings = get_settings()
    if n_sub is None:
        n_sub = settings.multi_query_count

    timeout = settings.query_ner_timeout_s

    try:
        if strategy == "hyde":
            return await asyncio.wait_for(_do_hyde(query), timeout=timeout)
        if strategy == "multi_query":
            return await asyncio.wait_for(_do_multi_query(query, n_sub), timeout=timeout)
        # 未知策略也走软降级（API 层 schema 已挡住，但 KB 配置可能写错）
        logger.warning("未知 query_rewrite 策略 %r，降级为 none", strategy)
        return _NOOP_RESULT

    except asyncio.TimeoutError:
        logger.warning("Query 改写超时（%.1fs，strategy=%s），降级为原 query 检索",
                       timeout, strategy)
        return _NOOP_RESULT
    except json.JSONDecodeError as e:
        logger.warning("multi_query JSON 解析失败：%s，降级为原 query", e)
        return _NOOP_RESULT
    except Exception as e:  # noqa: BLE001
        # 软失败：限流 / 网络抖动 / 模型异常都吞掉
        logger.warning("Query 改写调用失败（已忽略）：%s: %s",
                       type(e).__name__, e)
        return _NOOP_RESULT


__all__ = [
    "RewriteResult",
    "rewrite_query",
    "HYDE_SYSTEM_PROMPT",
    "MULTI_QUERY_SYSTEM_PROMPT",
]
