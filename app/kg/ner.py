"""LLM Prompt 驱动的通用命名实体识别（KG-05）。

V1.0 阶段实现路线：
- 复用 3.2 的 litellm.acompletion，绕开 LangGraph 走纯调用
- 强制 JSON 结构化输出（response_format={"type": "json_object"}）
- 实体类型固定 5 类：PERSON / LOCATION / ORG / TIME / OTHER（用户决策）

软失败原则：
- NER 是入库的辅助步骤，主链路 Milvus 写入是核心
- 抽风（LLM 限流、JSON 解析失败）时返回 []，不抛错、不阻断整批入库
- 错误细节走日志而非异常

替换路径：
- 未来想换专用 NER（spaCy / hanlp / 自训模型），只需保持
  run_ner(text) -> list[dict] 签名不变即可，调用方零感知
"""

from __future__ import annotations

import json
import logging
from typing import Any

import litellm

from app.core.config import get_settings

logger = logging.getLogger(__name__)


# ──────────────────── Prompt 定义 ────────────────────

# 实体类型枚举（与 PRD 通用 NER 约定一致）
ALLOWED_ENTITY_TYPES = {"PERSON", "LOCATION", "ORG", "TIME", "OTHER"}

NER_SYSTEM_PROMPT = """你是一个命名实体识别助手。从用户给的中文文本中抽取通用命名实体。

仅返回 JSON 对象，格式严格如下，不要任何其他文字：

{"entities": [{"name": "实体名", "type": "PERSON|LOCATION|ORG|TIME|OTHER"}]}

约束：
- 仅抽取明确出现在文本中的实体，不要推断
- 实体名保持原文写法，不做归一化（如"北京市"不要简化为"北京"）
- 同一实体在文本中多次出现，只输出一次
- 文本若无实体，返回 {"entities": []}
- type 必须是 PERSON / LOCATION / ORG / TIME / OTHER 五类之一
"""


# ──────────────────── 核心函数 ────────────────────


def _resolve_ner_kwargs(text: str) -> dict[str, Any]:
    """拼装 NER 调用的 LiteLLM 参数。

    优先用 KG_NER_MODEL；缺省则复用 LITELLM_MODEL（与 chat 同源）。
    """
    settings = get_settings()
    model = settings.kg_ner_model or settings.litellm_model
    if not model:
        raise ValueError(
            "NER 模型未配置：请在 .env 中设置 KG_NER_MODEL 或 LITELLM_MODEL"
        )

    # 自动补厂商前缀的逻辑复用 chat client 的策略（这里简化：
    # 模型名带 / 直接用；否则按 api_base 域名推断）
    if "/" not in model and settings.litellm_api_base:
        if "deepseek.com" in settings.litellm_api_base:
            model = f"deepseek/{model}"
        elif "dashscope.aliyuncs.com" in settings.litellm_api_base:
            model = f"dashscope/{model}"
        elif "open.bigmodel.cn" in settings.litellm_api_base:
            model = f"zhipu/{model}"

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": NER_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        # 强制 JSON 输出。注意：并非所有模型都支持此参数，不支持的会被静默忽略，
        # 此时我们靠 Prompt 中"仅返回 JSON"的硬性要求兜底
        "response_format": {"type": "json_object"},
        "timeout": settings.litellm_timeout,
        "num_retries": settings.litellm_num_retries,
    }
    if settings.litellm_api_key:
        kwargs["api_key"] = settings.litellm_api_key
    if settings.litellm_api_base:
        kwargs["api_base"] = settings.litellm_api_base
    return kwargs


def _parse_entities(content: str) -> list[dict]:
    """解析 LLM 返回的 JSON，做容错与去重。

    容错点：
    - LLM 偶尔会在 JSON 前后加 ```json 围栏
    - LLM 偶尔会用 PER 而不是 PERSON，做大小写归一与白名单过滤
    """
    text = content.strip()
    # 去掉 markdown 围栏
    if text.startswith("```"):
        # 去首行 ``` 或 ```json，去尾行 ```
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    data = json.loads(text)
    raw = data.get("entities") or []

    seen: set[tuple[str, str]] = set()
    cleaned: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        etype = (item.get("type") or "").strip().upper()
        if not name or not etype:
            continue
        if etype not in ALLOWED_ENTITY_TYPES:
            etype = "OTHER"
        key = (name, etype)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({"name": name, "type": etype})

    return cleaned


async def run_ner(text: str) -> list[dict]:
    """对单段文本做 NER，返回 [{"name":..., "type":...}, ...] 已去重。

    异常或解析失败时返回 []，**不抛错**（NER 失败不应阻断入库主链路）。

    Args:
        text: 输入文本（建议每次传一个 chunk，控制在 800 字符以内）

    Returns:
        实体列表；空列表表示无实体或抽取失败
    """
    if not text or not text.strip():
        return []

    try:
        kwargs = _resolve_ner_kwargs(text)
    except ValueError as e:
        logger.error("NER 配置错误：%s", e)
        return []

    try:
        resp = await litellm.acompletion(**kwargs)
        # 兼容 Pydantic 对象与裸 dict（与 chat client 同样的处理思路）
        if hasattr(resp, "model_dump"):
            resp_dict = resp.model_dump()
        else:
            resp_dict = resp

        content = resp_dict["choices"][0]["message"]["content"]
        entities = _parse_entities(content)
        logger.debug("NER 命中 %d 个实体（文本长度 %d）", len(entities), len(text))
        return entities

    except json.JSONDecodeError as e:
        logger.warning("NER 输出 JSON 解析失败：%s | 内容前 200 字: %r",
                       e, content[:200] if 'content' in locals() else "")
        return []
    except Exception as e:  # noqa: BLE001
        # 软失败：限流 / 网络抖动 / 模型异常都吞掉，记日志不阻断
        logger.warning("NER 调用失败（已忽略）：%s: %s", type(e).__name__, e)
        return []


__all__ = [
    "run_ner",
    "NER_SYSTEM_PROMPT",
    "ALLOWED_ENTITY_TYPES",
    "_parse_entities",  # 暴露给单测
]
