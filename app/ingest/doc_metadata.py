"""IDP-05 文档级元数据自动提取。

PRD §IDP-05：文档入库时调用 LLM 对全文（前 N 字符）进行元数据提取，结果存入
``kb_files.doc_metadata`` JSONB 字段，``summary_brief`` 单独字段；后续检索可
做前置过滤（doc_type / doc_date 等）。

提取字段（PRD §270）：
- ``doc_type``：合同 / 报告 / 手册 / 法规 / 其他
- ``doc_date``：YYYY-MM
- ``language``：zh / en / mixed
- ``key_topics``：3~5 个关键词，辅助 BM25 关键词扩展
- ``summary_brief``：不超过 100 字的文档摘要

软失败原则：LLM 限流 / JSON 解析失败 → 返 None，主链路把 doc_metadata 留空，
不阻断入库。

调用方：[app/tasks/ingest_task.py](../tasks/ingest_task.py) `_step_doc_metadata`。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import litellm

from app.core.config import get_settings
from app.ingest.parser import StructuredBlock
from app.ingest.table_description import _resolve_idp_kwargs

logger = logging.getLogger(__name__)


# ──────────────────── Prompt ────────────────────

# 中文双引号避免 ASCII 闭合问题；JSON 用 ASCII（LLM 输出 JSON 必须 ASCII 引号）
DOC_META_SYSTEM_PROMPT = """从以下文档内容中提取结构化元数据，仅返回 JSON 对象，不要任何其他文字：

{"doc_type": "合同|报告|手册|法规|其他", "doc_date": "YYYY-MM" 或 null, "language": "zh|en|mixed", "key_topics": ["关键词1", "关键词2"], "summary_brief": "不超过 100 字的文档摘要"}

约束：
- 严格按以上字段输出，不要新增字段
- summary_brief 不超过 100 字（约 200 字节）
- key_topics 是字符串数组，3~5 个核心关键词
- doc_type 在 5 个枚举之一；模糊时填“其他”
- 字段无法判断时填 null（key_topics 填空数组 []）"""


# summary_brief 字节兜底：100 字中文 ≈ 300 字节；放宽到 400 防边界
_MAX_SUMMARY_BRIEF_BYTES = 400
# key_topics 单元素字节兜底
_MAX_TOPIC_BYTES = 64
_MAX_TOPICS = 8

_VALID_DOC_TYPES = {"合同", "报告", "手册", "法规", "其他"}
_VALID_LANGUAGES = {"zh", "en", "mixed"}


# ──────────────────── 数据类 ────────────────────


@dataclass(frozen=True)
class DocMetadata:
    """文档级元数据（IDP-05 输出）。"""

    doc_type: str | None = None
    doc_date: str | None = None
    language: str | None = None
    key_topics: list[str] = field(default_factory=list)
    summary_brief: str | None = None

    def to_dict(self) -> dict:
        """转为可写入 JSONB 的纯 dict。``summary_brief`` 单独字段，不放 dict 里。"""
        return {
            "doc_type": self.doc_type,
            "doc_date": self.doc_date,
            "language": self.language,
            "key_topics": list(self.key_topics),
        }


# ──────────────────── 工具 ────────────────────


def _truncate_utf8(s: str, max_bytes: int) -> str:
    """按 UTF-8 字节安全截断；不切断多字节字符。"""
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


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


def _parse_metadata(content: str) -> DocMetadata | None:
    """解析 LLM JSON 输出 → DocMetadata；解析失败返 None。"""
    try:
        data = json.loads(_strip_code_fence(content))
    except json.JSONDecodeError as e:
        logger.warning("IDP-05 JSON 解析失败：%s | 内容前 200 字: %r", e, content[:200])
        return None

    if not isinstance(data, dict):
        return None

    # doc_type：白名单校验
    doc_type = data.get("doc_type")
    if doc_type not in _VALID_DOC_TYPES:
        doc_type = None  # 非法值置空，不强行归到"其他"

    # doc_date：保留原字符串（YYYY-MM 格式由 LLM 自己保证；这里不强校验）
    doc_date = data.get("doc_date")
    if doc_date is not None and not isinstance(doc_date, str):
        doc_date = None

    # language：白名单校验
    language = data.get("language")
    if language not in _VALID_LANGUAGES:
        language = None

    # key_topics：列表清洗 + 字节截断 + 上限
    raw_topics = data.get("key_topics") or []
    topics: list[str] = []
    if isinstance(raw_topics, list):
        seen: set[str] = set()
        for t in raw_topics:
            if not isinstance(t, str):
                continue
            cleaned = _truncate_utf8(t.strip(), _MAX_TOPIC_BYTES)
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            topics.append(cleaned)
            if len(topics) >= _MAX_TOPICS:
                break

    # summary_brief：字节截断
    summary_brief = data.get("summary_brief")
    if isinstance(summary_brief, str):
        summary_brief = _truncate_utf8(summary_brief.strip(), _MAX_SUMMARY_BRIEF_BYTES)
        if not summary_brief:
            summary_brief = None
    else:
        summary_brief = None

    return DocMetadata(
        doc_type=doc_type,
        doc_date=doc_date,
        language=language,
        key_topics=topics,
        summary_brief=summary_brief,
    )


def _join_blocks_text(blocks: list[StructuredBlock], max_chars: int) -> str:
    """拼接 blocks 的 content 字段，截断到 max_chars。

    优先取靠前的 blocks（文档开头通常含标题/摘要/作者等关键信息）。
    """
    pieces: list[str] = []
    total = 0
    for block in blocks:
        content = block.content or ""
        if not content:
            continue
        # 标题块前加换行强调结构
        if block.block_type == "heading":
            pieces.append(f"\n# {content}")
        else:
            pieces.append(content)
        total += len(content)
        if total >= max_chars:
            break

    joined = "\n".join(pieces)
    if len(joined) > max_chars:
        joined = joined[:max_chars]
    return joined


# ──────────────────── 主入口 ────────────────────


async def extract_doc_metadata(
    blocks: list[StructuredBlock],
) -> DocMetadata | None:
    """对文档前 N 字符做元数据提取。

    Returns:
        DocMetadata；任何异常/超时/解析失败一律返 ``None``（软失败），主链路
        会把 ``kb_files.doc_metadata`` 与 ``summary_brief`` 留空。
    """
    if not blocks:
        return None

    settings = get_settings()
    timeout = settings.idp_llm_timeout_s
    max_chars = settings.idp_doc_meta_input_chars

    text = _join_blocks_text(blocks, max_chars)
    if not text.strip():
        return None

    try:
        kwargs = _resolve_idp_kwargs(
            messages=[
                {"role": "system", "content": DOC_META_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ]
        )
        # 强制 JSON 输出（不支持的模型靠 prompt 兜底）
        kwargs["response_format"] = {"type": "json_object"}
        kwargs["max_tokens"] = 800
        resp = await asyncio.wait_for(litellm.acompletion(**kwargs), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("IDP-05 文档元数据提取超时 timeout=%.1fs", timeout)
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("IDP-05 文档元数据提取失败 err=%s: %s", type(e).__name__, e)
        return None

    if hasattr(resp, "model_dump"):
        resp = resp.model_dump()
    content = resp["choices"][0]["message"]["content"] or ""
    return _parse_metadata(content)


__all__ = [
    "DocMetadata",
    "DOC_META_SYSTEM_PROMPT",
    "extract_doc_metadata",
    "_parse_metadata",
]
