"""Embedding 客户端：通过 LiteLLM 调用远程 Embedding 模型。

为什么不复用 app/llm/client.py？
- chat 模型与 embedding 模型经常**不同源**：chat 走 DeepSeek，embedding 走
  SiliconFlow 的 Qwen3-Embedding-8B。配置项分开后切换更直观。
- chat 客户端的 _resolve_model_name 假设的是 chat 厂商域名，硬复用过来会误判。

设计要点：
1. 维度严格校验：返回向量长度必须等于 settings.embedding_dimension，
   否则直接抛错。这是为了防止 Milvus 写入时才暴露维度不匹配（很难定位）。
2. 批量友好：input 接收 list[str]，自动批量请求，返回顺序与输入对齐。
3. 异常透传：不在此层包装/吞掉 LiteLLM 异常，由调用方决定如何处理。
"""

from __future__ import annotations

import logging
import time
from typing import Any

import litellm

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _build_kwargs(input_texts: list[str]) -> dict[str, Any]:
    """拼装 litellm.aembedding 的请求参数。"""
    settings = get_settings()

    if not settings.embedding_model:
        raise ValueError(
            "EMBEDDING_MODEL 未配置。请在 .env 中设置，例如："
            "EMBEDDING_MODEL=openai/Qwen/Qwen3-Embedding-8B"
        )

    kwargs: dict[str, Any] = {
        "model": settings.embedding_model,
        "input": input_texts,
    }

    if settings.embedding_api_key:
        kwargs["api_key"] = settings.embedding_api_key
    if settings.embedding_api_base:
        kwargs["api_base"] = settings.embedding_api_base

    # dimensions 参数：故意不传。
    # 原因：LiteLLM 的 openai/ 路由会以 "Setting dimensions is not supported for
    # OpenAI text-embedding-3 and later models" 为由拒绝这个参数（即便底层
    # 实际是 SiliconFlow / 阿里 DashScope 而不是真 OpenAI）。
    # 实际维度由模型本身决定（如 Qwen3-Embedding-8B 默认 4096 维），
    # 返回后由 aembed_texts() 做严格校验，与 EMBEDDING_DIMENSION 不一致则抛错。
    # 想换 Embedding 模型时，确保模型默认输出维度等于 EMBEDDING_DIMENSION 即可。

    return kwargs


async def aembed_texts(texts: list[str]) -> list[list[float]]:
    """批量将文本转为向量。

    Args:
        texts: 文本列表（不能为空）

    Returns:
        二维浮点列表，长度与 texts 一致，每个内层列表长度为 embedding_dimension

    Raises:
        ValueError: 入参为空 / EMBEDDING_MODEL 未配置 / 返回维度不一致
        litellm.*:   底层 LiteLLM 异常（超时、认证失败、限流等）
    """
    if not texts:
        raise ValueError("aembed_texts: texts 不能为空")

    settings = get_settings()
    kwargs = _build_kwargs(texts)

    t0 = time.perf_counter()
    logger.info(
        "Embedding 请求: model=%s n=%d dim=%d",
        kwargs["model"],
        len(texts),
        settings.embedding_dimension,
    )

    resp = await litellm.aembedding(**kwargs)

    # LiteLLM 返回 OpenAI 标准格式：
    #   {"data": [{"embedding": [...], "index": 0}, ...], "usage": {...}, ...}
    # 兼容 Pydantic 对象与裸 dict 两种形态（与 chat client 同样的处理思路）
    if hasattr(resp, "model_dump"):
        resp_dict = resp.model_dump()
    elif hasattr(resp, "dict"):
        resp_dict = resp.dict()
    else:
        resp_dict = resp

    data = resp_dict.get("data") or []
    if len(data) != len(texts):
        raise ValueError(
            f"Embedding 返回条数与输入不一致：input={len(texts)} got={len(data)}"
        )

    # 按 index 排序（部分厂商会乱序返回）后取 embedding
    data_sorted = sorted(data, key=lambda x: x.get("index", 0))
    vectors: list[list[float]] = [item["embedding"] for item in data_sorted]

    # 维度严格校验
    expected_dim = settings.embedding_dimension
    for i, vec in enumerate(vectors):
        if len(vec) != expected_dim:
            raise ValueError(
                f"Embedding 维度不匹配：index={i} expected={expected_dim} got={len(vec)}。"
                "请检查 EMBEDDING_DIMENSION 是否与所选模型实际输出维度一致。"
            )

    usage = resp_dict.get("usage", {})
    elapsed = time.perf_counter() - t0
    logger.info(
        "Embedding 响应: model=%s n=%d tokens=%s %.2fs",
        kwargs["model"],
        len(vectors),
        usage.get("total_tokens", "?"),
        elapsed,
    )

    return vectors


__all__ = ["aembed_texts"]
