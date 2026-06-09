"""LiteLLM 调用封装：统一接入各类 LLM 厂商。

对外暴露两个核心函数：
  - acompletion: 一次性返回完整回复（dict）
  - astream:     流式返回 chunk 序列（每条也是 dict）

设计决策：
1. 自动模型前缀补全 —— LiteLLM 要求模型名带厂商前缀（如 deepseek/xxx），
   用户 .env 里的 LITELLM_MODEL 有时不带，此处根据 api_base 推断并补齐。
2. 超时 & 重试 —— 通过 litellm 内置的 timeout / num_retries 透传，
   不在应用层自造重试逻辑。
3. 日志 —— 每次请求记录模型名、token 用量、耗时，便于排查。
4. 返回值统一为 dict —— LiteLLM 实际返回 OpenAI 的 Pydantic 对象
   （ModelResponse / CustomStreamWrapper），不同厂商/版本的对象结构不稳定。
   在 client 层统一 model_dump() 为 dict，下游（LangGraph、service）只需
   处理标准 JSON 结构，零感知 LiteLLM 内部类型。
"""

import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import litellm

from app.core.config import get_settings
from app.llm.messages import Message, ToolDefinition

logger = logging.getLogger(__name__)

# 关闭 litellm 自带的冗长日志
litellm.suppress_debug_info = True


# ────────────── 模型名自动修正 ──────────────

# 已知的 (api_base 子串, 前缀) 映射
_VENDOR_PREFIX_MAP: list[tuple[str, str]] = [
    ("deepseek.com", "deepseek"),
    ("dashscope.aliyuncs.com", "dashscope"),
    ("open.bigmodel.cn", "zhipu"),
    ("api.openai.com", "openai"),
]


def _resolve_model_name(model: str | None, api_base: str | None) -> str:
    """根据 api_base 推断并补齐 LiteLLM 厂商前缀。

    若 model 已包含 '/'（认为用户显式写了厂商前缀），直接返回。
    否则根据 api_base 匹配已知域名，自动补前缀。
    """
    if not model:
        raise ValueError("LITELLM_MODEL 未配置。请在 .env 中设置，例如 LITELLM_MODEL=deepseek/deepseek-chat")

    # 用户已写明前缀
    if "/" in model:
        return model

    # 根据 api_base 推断
    if api_base:
        for domain_frag, prefix in _VENDOR_PREFIX_MAP:
            if domain_frag in api_base:
                resolved = f"{prefix}/{model}"
                logger.info("模型名自动补前缀: %s -> %s", model, resolved)
                return resolved

    # 无法推断，直接透传（可能 LiteLLM 自己能处理）
    logger.warning("无法推断模型前缀，将原样传给 LiteLLM: model=%s api_base=%s", model, api_base)
    return model


# ────────────── 公共调用参数 ──────────────


def _build_kwargs(
    messages: list[Message],
    tools: list[ToolDefinition] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """拼装 litellm.acompletion / acompletion(stream=True) 的公共 kwargs。"""
    settings = get_settings()

    model = _resolve_model_name(settings.litellm_model, settings.litellm_api_base)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "timeout": settings.litellm_timeout,
        "num_retries": settings.litellm_num_retries,
    }

    if settings.litellm_api_key:
        kwargs["api_key"] = settings.litellm_api_key
    if settings.litellm_api_base:
        kwargs["api_base"] = settings.litellm_api_base

    if tools:
        kwargs["tools"] = tools

    kwargs.update(extra)
    return kwargs


def _to_dict(obj: Any) -> Any:
    """将 LiteLLM 返回的 Pydantic 对象递归转为 dict。

    LiteLLM 返回 OpenAI 规范的 ModelResponse / CustomStreamWrapper，
    内部是 Pydantic v1/v2 对象。用 model_dump() 转 dict，
    兼容 Pydantic v1 的 dict() 和 v2 的 model_dump()。
    """
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    # 对于普通 dict / list / 原始类型，直接返回
    return obj


# ────────────── 一次性调用 ──────────────


async def acompletion(
    messages: list[Message],
    tools: list[ToolDefinition] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """一次性调用 LLM 并返回完整响应（dict）。

    返回值结构：
        {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "...",             # 可能为 None
                    "tool_calls": [...]           # 模型主动调用工具时存在
                },
                "finish_reason": "stop" | "tool_calls"
            }],
            "usage": {"prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ...},
            ...
        }

    Raises:
        ValueError: LITELLM_MODEL 未配置
        litellm.*:   LiteLLM 底层异常（超时、认证失败、限流等）
    """
    kwargs = _build_kwargs(messages, tools, **extra)

    t0 = time.perf_counter()
    logger.info("LLM 请求: model=%s messages=%d tools=%s", kwargs["model"], len(messages), "yes" if tools else "no")

    resp = await litellm.acompletion(**kwargs)

    elapsed = time.perf_counter() - t0
    result = _to_dict(resp)

    usage = result.get("usage", {})
    logger.info(
        "LLM 响应: model=%s finish=%s tokens=%s %.2fs",
        kwargs["model"],
        result["choices"][0].get("finish_reason", "?"),
        usage.get("total_tokens", "?"),
        elapsed,
    )
    return result


# ────────────── 流式调用 ──────────────


async def astream(
    messages: list[Message],
    tools: list[ToolDefinition] | None = None,
    **extra: Any,
) -> AsyncIterator[dict[str, Any]]:
    """流式调用 LLM，逐 chunk 返回（每个 chunk 也是 dict）。

    每个 chunk 结构：
        {
            "choices": [{
                "delta": {
                    "role": "assistant",      # 仅首 chunk
                    "content": "部分文本",     # 后续 chunk
                    "tool_calls": [...]        # 工具调用增量
                },
                "finish_reason": None | "stop" | "tool_calls"
            }]
        }

    3.3 阶段 LangGraph 的 ReAct 循环会通过此接口获取打字机式文本流。
    """
    kwargs = _build_kwargs(messages, tools, **extra, stream=True)

    logger.info("LLM 流式请求: model=%s messages=%d tools=%s", kwargs["model"], len(messages), "yes" if tools else "no")
    t0 = time.perf_counter()

    async for chunk in await litellm.acompletion(**kwargs):
        yield _to_dict(chunk)

    elapsed = time.perf_counter() - t0
    logger.info("LLM 流式完成: model=%s %.2fs", kwargs["model"], elapsed)
