"""IDP-03 表格自然语言描述生成。

PRD §IDP-03：表格内容对向量检索极不友好（列名 + 数据分离导致语义残缺）。
对每张识别到的表格，调用 LLM 生成一段自然语言描述，作为额外 Chunk 同步入库，
参与向量检索。原表格 Chunk 不变。

设计要点：
1. **薄封装** litellm.acompletion（参考 [app/kg/ner.py](../kg/ner.py) 模式）
2. **硬超时**：每次 LLM 调用包 ``asyncio.wait_for(idp_llm_timeout_s)``
3. **软失败**：单张表生成失败 → 该表不产出 description，不阻断其他表
4. **并发限流**：``asyncio.Semaphore(idp_concurrency)`` 防压垮 LLM API
5. **字节安全截断**：description 超过 600 字节按 UTF-8 安全截断（防 Milvus 写入意外）

调用方：[app/tasks/ingest_task.py](../tasks/ingest_task.py) `_step_table_description`。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import litellm

from app.core.async_utils import gather_with_timeout
from app.core.config import get_settings
from app.ingest.structured_splitter import StructuredChunk
from app.llm.client import build_completion_kwargs

logger = logging.getLogger(__name__)


# ──────────────────── Prompt ────────────────────

# PRD §IDP-03 推荐 prompt；中文双引号避免 ASCII " 与 docstring 闭合冲突
TABLE_DESC_SYSTEM_PROMPT = """将以下 Markdown 表格转化为一段自然语言描述：
1. 描述表格的主题和结构
2. 提炼表格中的关键数据和规律
3. 不超过 200 字
4. 不要使用“该表格”等冗余开头
5. 直接输出描述文本，不要 markdown 围栏、不要前缀"""


# 描述长度兜底：太长会触发 Milvus VARCHAR 限制；600 字节中文约 200 字
_MAX_DESCRIPTION_BYTES = 600


# ──────────────────── 数据类 ────────────────────


@dataclass(frozen=True)
class TableDescription:
    """单张表格的自然语言描述。"""

    parent_index: int  # 在 fine_chunks 列表中的下标（注意：不是 StructuredChunk.index）
    description: str


# ──────────────────── LLM 工具 ────────────────────


def _resolve_idp_kwargs(messages: list[dict]) -> dict[str, Any]:
    """拼装 IDP LLM 调用 kwargs。

    优先用 IDP_LLM_MODEL；缺省复用 LITELLM_MODEL。厂商前缀推断逻辑与
    [app/kg/ner.py](../kg/ner.py) 保持一致。
    """
    settings = get_settings()
    return build_completion_kwargs(
        messages=messages,
        model=settings.idp_llm_model,
        fallback_model=settings.litellm_model,
        required_model_label="IDP_LLM_MODEL 或 LITELLM_MODEL",
        temperature=0.3,
        max_tokens=600,
        settings_obj=settings,
    )


def _truncate_utf8(s: str, max_bytes: int) -> str:
    """按 UTF-8 字节安全截断；不切断多字节字符。"""
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _extract_content(resp: Any) -> str:
    """从 LiteLLM 响应中安全取出 message.content。"""
    if hasattr(resp, "model_dump"):
        resp = resp.model_dump()
    return resp["choices"][0]["message"]["content"] or ""


# ──────────────────── 单表描述生成 ────────────────────


async def _describe_one_table(
    *,
    parent_index: int,
    table_content: str,
    timeout: float,
    sem: asyncio.Semaphore,
) -> TableDescription | None:
    """对单张表格生成自然语言描述。

    Returns:
        成功返回 TableDescription；任何异常/超时/空内容返 None。
    """
    async with sem:
        try:
            kwargs = _resolve_idp_kwargs(
                messages=[
                    {"role": "system", "content": TABLE_DESC_SYSTEM_PROMPT},
                    {"role": "user", "content": table_content},
                ]
            )
            resp = await asyncio.wait_for(litellm.acompletion(**kwargs), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "IDP-03 表格描述超时 parent_index=%d timeout=%.1fs", parent_index, timeout
            )
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "IDP-03 表格描述失败 parent_index=%d err=%s: %s",
                parent_index, type(e).__name__, e,
            )
            return None

    text = _extract_content(resp).strip()
    if len(text) < 5:
        # 描述过短视为生成退化，跳过
        logger.warning("IDP-03 表格描述过短（< 5 字），跳过 parent_index=%d", parent_index)
        return None

    # 字节安全截断防 Milvus VARCHAR 意外
    text = _truncate_utf8(text, _MAX_DESCRIPTION_BYTES)
    return TableDescription(parent_index=parent_index, description=text)


# ──────────────────── 主入口 ────────────────────


async def generate_table_descriptions(
    fine_chunks: list[StructuredChunk],
) -> list[TableDescription]:
    """对所有 ``block_type=="table"`` 的细粒度 chunk 并发生成描述。

    Args:
        fine_chunks: 细粒度切片产物（IDP-02 输出）

    Returns:
        描述列表；无表格 chunk / 全部生成失败时返 ``[]``。失败的单张表
        软失败跳过，不影响其他表。
    """
    settings = get_settings()
    timeout = settings.idp_llm_timeout_s
    concurrency = settings.idp_concurrency

    # 找出所有表格 chunk 的下标
    table_indices = [
        i for i, c in enumerate(fine_chunks) if c.block_type == "table"
    ]
    if not table_indices:
        return []

    sem = asyncio.Semaphore(concurrency)
    coros = [
        _describe_one_table(
            parent_index=i,
            table_content=fine_chunks[i].content,
            timeout=timeout,
            sem=sem,
        )
        for i in table_indices
    ]
    try:
        results = await gather_with_timeout(
            coros,
            timeout_s=max(timeout + 5, len(coros) * timeout / concurrency + 5),
            label="idp_table_description",
            return_exceptions=True,
        )
    except asyncio.TimeoutError:
        logger.warning("IDP-03 表格描述整组超时（软失败），跳过本步 total_tables=%d", len(table_indices))
        return []

    descriptions: list[TableDescription] = []
    for r in results:
        if isinstance(r, Exception) or r is None:
            continue
        descriptions.append(r)

    logger.info(
        "IDP-03 表格描述完成 total_tables=%d generated=%d",
        len(table_indices), len(descriptions),
    )
    return descriptions


__all__ = [
    "TableDescription",
    "TABLE_DESC_SYSTEM_PROMPT",
    "generate_table_descriptions",
    "_truncate_utf8",
]
