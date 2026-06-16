"""V2.0 IDP-04 双层索引（Hierarchical Index）。

PRD §IDP-04：对同一文档同时建立两个粒度的向量索引：

- **粗粒度（segment summary）**：按父级标题聚合相邻细粒度 chunk，调 LLM 生成摘要；
  is_summary=True，初筛阶段使用，覆盖更广
- **细粒度（fine chunk）**：IDP-02 产出的原始切片；is_summary=False，
  通过 parent_chunk_id 串回粗粒度，精排阶段使用

聚合策略（T7 决策）：**按父级 heading_path 聚合**——取 chunk 的
``heading_path[:-1]`` 作为 group key（最末一级标题之外的祖先路径）。
- 空 heading_path 的 chunk 单独一组
- 仅 1 个 chunk 的组也保留（粗粒度索引保持完整）
- 表格 / 代码 chunk 也参与分组

软失败原则：单组摘要失败 → 跳过该组（不生成对应粗 chunk）；该组的细粒度
chunk 的 parent_chunk_id 保持 None。

调用方：[app/tasks/ingest_task.py](../tasks/ingest_task.py) `_step_dual_layer_index`。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import litellm

from app.core.config import get_settings
from app.ingest.structured_splitter import StructuredChunk
from app.ingest.table_description import _resolve_idp_kwargs, _truncate_utf8

logger = logging.getLogger(__name__)


# ──────────────────── Prompt ────────────────────

# 中文双引号避免 ASCII 闭合问题
SUMMARY_SYSTEM_PROMPT = """对以下文档片段生成一段简明摘要：
1. 提炼核心论点和关键事实
2. 不超过 300 字
3. 保持原文语言风格
4. 直接输出摘要，不要前缀（如“摘要：”）、不要 markdown 围栏"""


# 摘要长度兜底：太长触发 Milvus VARCHAR 限制；900 字节中文约 300 字
_MAX_SUMMARY_BYTES = 900


# ──────────────────── 数据类 ────────────────────


@dataclass(frozen=True)
class CoarseChunk:
    """粗粒度摘要 chunk 中间产物。

    最终会被转换为 ``StructuredChunk(is_summary=True)`` 写入 Milvus；
    转换在 ``_step_dual_layer_index`` 中完成（需要 document_id 算 chunk_id）。
    """

    parent_indices: list[int]  # 关联的细粒度 chunk 在 fine_chunks 中的下标列表
    heading_path: list[str]
    summary_text: str
    page_number: int | None = None


# ──────────────────── 分组工具 ────────────────────


def _group_key(chunk: StructuredChunk) -> tuple[str, ...]:
    """计算 chunk 的分组 key（父级 heading_path）。

    取 ``heading_path[:-1]`` 作为 key，让同一父级下的兄弟 chunk 聚合：
    - [第1章, 1.1节] 和 [第1章, 1.2节] 的 key 都是 (第1章,)，会聚合
    - [第1章] 单独一组（key=()）
    - heading_path=[] 的 chunk 也归到 () 组（与无标题 chunk 同组）

    返回 tuple 而非 list，确保可哈希（用作 dict key）。
    """
    if len(chunk.heading_path) <= 1:
        return ()
    return tuple(chunk.heading_path[:-1])


def group_by_parent_heading(
    fine_chunks: list[StructuredChunk],
) -> list[list[int]]:
    """按父级 heading_path 把细粒度 chunk 分组。

    Returns:
        每个内层 list 是一组 fine_chunks 下标；保持原有出现顺序，便于后续摘要时
        按文档顺序拼接 chunk 内容。
    """
    if not fine_chunks:
        return []

    groups: dict[tuple[str, ...], list[int]] = {}
    order: list[tuple[str, ...]] = []  # 保持组首次出现的顺序

    for i, chunk in enumerate(fine_chunks):
        key = _group_key(chunk)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(i)

    return [groups[k] for k in order]


# ──────────────────── 单组摘要 ────────────────────


async def _summarize_one_group(
    *,
    fine_chunks: list[StructuredChunk],
    indices: list[int],
    timeout: float,
    sem: asyncio.Semaphore,
) -> CoarseChunk | None:
    """对单组 fine_chunks 拼接 + LLM 摘要 → CoarseChunk。失败返 None。"""
    if not indices:
        return None

    # 拼接组内 chunk 内容（保持文档顺序）
    pieces = [fine_chunks[i].content for i in indices]
    joined = "\n\n".join(pieces)

    # 输入截断防超长（粗略上限：8000 字符 ≈ 4000 token）
    if len(joined) > 8000:
        joined = joined[:8000]

    # 取首个 chunk 的 heading_path 作为粗 chunk 的 heading_path
    # （组内所有 chunk 共享父级 heading_path[:-1]，但完整路径可能不同；取第一个即可）
    head = fine_chunks[indices[0]]

    async with sem:
        try:
            kwargs = _resolve_idp_kwargs(
                messages=[
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": joined},
                ]
            )
            kwargs["max_tokens"] = 800
            resp = await asyncio.wait_for(litellm.acompletion(**kwargs), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "IDP-04 段落摘要超时 group_size=%d timeout=%.1fs",
                len(indices), timeout,
            )
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "IDP-04 段落摘要失败 group_size=%d err=%s: %s",
                len(indices), type(e).__name__, e,
            )
            return None

    if hasattr(resp, "model_dump"):
        resp = resp.model_dump()
    text = (resp["choices"][0]["message"]["content"] or "").strip()
    if len(text) < 5:
        logger.warning("IDP-04 摘要过短（< 5 字），跳过组 group_size=%d", len(indices))
        return None

    text = _truncate_utf8(text, _MAX_SUMMARY_BYTES)

    # 用首个 chunk 的 heading_path 作为粗 chunk 的 path（保持完整路径，便于追溯）
    coarse_heading = list(head.heading_path)
    # 用首个 chunk 的 page_number 作为粗 chunk 的页码
    coarse_page = head.page_number

    return CoarseChunk(
        parent_indices=indices,
        heading_path=coarse_heading,
        summary_text=text,
        page_number=coarse_page,
    )


# ──────────────────── 主入口 ────────────────────


async def generate_coarse_chunks(
    fine_chunks: list[StructuredChunk],
) -> list[CoarseChunk]:
    """生成粗粒度摘要 chunk。

    Args:
        fine_chunks: 细粒度切片产物（**不包含** table_description chunk —— 它们
            已在主链路中分离）

    Returns:
        粗粒度摘要列表；空列表表示「无 fine_chunks / 全部摘要失败」，调用方应
        把 fine chunks 直接写入（parent_chunk_id=None / is_summary=False）。
        ``settings.idp_dual_index_enable=False`` 时永远返 ``[]``。
    """
    settings = get_settings()

    if not settings.idp_dual_index_enable:
        logger.info("IDP-04 双层索引开关关闭（IDP_DUAL_INDEX_ENABLE=False），跳过粗粒度生成")
        return []

    if not fine_chunks:
        return []

    timeout = settings.idp_llm_timeout_s
    concurrency = settings.idp_concurrency

    groups = group_by_parent_heading(fine_chunks)
    sem = asyncio.Semaphore(concurrency)
    coros = [
        _summarize_one_group(
            fine_chunks=fine_chunks,
            indices=indices,
            timeout=timeout,
            sem=sem,
        )
        for indices in groups
    ]
    results = await asyncio.gather(*coros, return_exceptions=True)

    coarse: list[CoarseChunk] = []
    for r in results:
        if isinstance(r, Exception) or r is None:
            continue
        coarse.append(r)

    logger.info(
        "IDP-04 双层索引完成 fine=%d groups=%d coarse_generated=%d",
        len(fine_chunks), len(groups), len(coarse),
    )
    return coarse


__all__ = [
    "CoarseChunk",
    "SUMMARY_SYSTEM_PROMPT",
    "group_by_parent_heading",
    "generate_coarse_chunks",
]
