"""结构感知文本切片器（V2.0 IDP-02）。

与 V1.5 的 `splitter.py`（纯文本切片）不同，V2.0 基于 `StructuredBlock` 的元数据
做智能切片，保留结构信息。

切片优先级（PRD §IDP-02）：
1. 代码块 → 整块保留，不切断
2. 表格块 → 整块保留，不切断
3. 标题 + 紧随的段落 → 组合到一个 chunk
4. 普通段落 → 超长时递归切分，兜底策略

每个输出 `StructuredChunk` 携带 heading_path / block_type / page_number / position_index
等元数据，后续写入 Milvus V2 Schema 时直接映射。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Literal

from app.ingest.parser import StructuredBlock

logger = logging.getLogger(__name__)


# ──────────────── 输出类型 ────────────────


@dataclass(frozen=True)
class StructuredChunk:
    """V2.0 结构感知切片产物（IDP-02）。

    与 V1.5 的 Chunk 相比，多了结构元数据字段。
    这些字段在写入 Milvus V2 Schema 时直接映射到对应列。
    """

    chunk_id: str  # uuid，全局唯一
    index: int  # 切片序号，从 0 开始
    content: str  # 切片文本内容
    heading_path: list[str]  # 标题层级路径
    block_type: Literal["paragraph", "heading", "table", "code", "list", "mixed"]
    page_number: int | None  # 页码
    position_index: int  # 文档内序号（取 chunk 首个 block 的）
    parent_chunk_id: str | None  # 父 chunk ID（双层索引，T7 才用）
    is_summary: bool  # 是否为摘要 chunk（T7 才用）


def _make_chunk_id() -> str:
    """生成唯一 chunk_id。"""
    return uuid.uuid4().hex


# ──────────────── tiktoken 长度函数 ────────────────

# 复用 splitter.py 同款 tiktoken 逻辑
_TOKEN_LEN_FN = None


def _get_token_len_fn():
    """构造 tiktoken 长度函数；导入失败时退化为 len(text)。"""
    global _TOKEN_LEN_FN
    if _TOKEN_LEN_FN is not None:
        return _TOKEN_LEN_FN

    try:
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")

        def _len(text: str) -> int:
            return len(encoding.encode(text))

        _TOKEN_LEN_FN = _len
    except ImportError:
        logger.warning("tiktoken 未安装，切片长度退化为 len(text)")
        _TOKEN_LEN_FN = len

    return _TOKEN_LEN_FN


# ──────────────── 切片逻辑 ────────────────


def split_structured_blocks(
    blocks: list[StructuredBlock],
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> list[StructuredChunk]:
    """把 StructuredBlock 列表切成 StructuredChunk 列表。

    切片策略（IDP-02 优先级）：
    1. **代码块** → 整块保留为一个 chunk，不论多长（代码切断后不可读）
    2. **表格块** → 整块保留为一个 chunk，不论多长（表格切断后语义丢失）
    3. **标题 + 后续段落组合** → 标题与紧跟的段落组合到一个 chunk，
       直到接近 chunk_size 或遇到下一个标题
    4. **超长段落** → 用 LangChain RecursiveCharacterTextSplitter 兜底切分

    Args:
        blocks: parse_document_structured() 的输出
        chunk_size: 单个切片最大 token 数
        chunk_overlap: 相邻切片重叠 token 数

    Returns:
        StructuredChunk 列表，按文档顺序，index 从 0 开始
    """
    if chunk_size <= 0:
        raise ValueError(f"chunk_size 必须 > 0，得到 {chunk_size}")
    if chunk_overlap < 0:
        raise ValueError(f"chunk_overlap 必须 >= 0，得到 {chunk_overlap}")

    if not blocks:
        return []

    token_len = _get_token_len_fn()
    chunks: list[StructuredChunk] = []
    chunk_index = 0

    i = 0
    while i < len(blocks):
        block = blocks[i]

        # ── 代码块：整块保留 ──
        if block.block_type == "code":
            chunks.append(
                _make_chunk(
                    chunk_index=chunk_index,
                    blocks=[block],
                    block_type="code",
                )
            )
            chunk_index += 1
            i += 1
            continue

        # ── 表格块：整块保留 ──
        if block.block_type == "table":
            chunks.append(
                _make_chunk(
                    chunk_index=chunk_index,
                    blocks=[block],
                    block_type="table",
                )
            )
            chunk_index += 1
            i += 1
            continue

        # ── 列表块：整块保留 ──
        if block.block_type == "list":
            chunks.append(
                _make_chunk(
                    chunk_index=chunk_index,
                    blocks=[block],
                    block_type="list",
                )
            )
            chunk_index += 1
            i += 1
            continue

        # ── 标题 + 后续段落组合 ──
        if block.block_type == "heading":
            collected_blocks: list[StructuredBlock] = [block]
            collected_tokens = token_len(block.content)
            j = i + 1

            # 收集后续段落直到达到 chunk_size 或遇到下一个标题/独立块
            while j < len(blocks):
                next_block = blocks[j]
                # 遇到下一个标题或独立块（代码/表格/列表）时停止
                if next_block.block_type in ("heading", "code", "table", "list"):
                    break

                next_tokens = token_len(next_block.content)
                if collected_tokens + next_tokens > chunk_size and collected_tokens > 0:
                    # 当前 chunk 已经够大了
                    # 但如果还没收集任何段落（只有标题），必须至少加一段
                    if len(collected_blocks) > 1:
                        break

                collected_blocks.append(next_block)
                collected_tokens += next_tokens
                j += 1

            # 如果组合后超长，需要拆分
            if collected_tokens > chunk_size * 1.5 and len(collected_blocks) > 1:
                # 拆分：标题单独一个 chunk，段落另切
                # 标题 chunk
                chunks.append(
                    _make_chunk(
                        chunk_index=chunk_index,
                        blocks=[block],
                        block_type="heading",
                    )
                )
                chunk_index += 1

                # 段落部分：尝试合并或逐个切
                para_blocks = collected_blocks[1:]
                for para_chunk in _split_paragraphs(
                    para_blocks, chunk_size, chunk_overlap, token_len, chunk_index
                ):
                    chunks.append(para_chunk)
                    chunk_index += 1
            else:
                chunks.append(
                    _make_chunk(
                        chunk_index=chunk_index,
                        blocks=collected_blocks,
                        block_type="mixed" if len(collected_blocks) > 1 else "heading",
                    )
                )
                chunk_index += 1

            i = j
            continue

        # ── 普通段落（不在标题后的）──
        if block.block_type == "paragraph":
            block_tokens = token_len(block.content)

            if block_tokens <= chunk_size:
                # 短段落直接成一个 chunk
                chunks.append(
                    _make_chunk(
                        chunk_index=chunk_index,
                        blocks=[block],
                        block_type="paragraph",
                    )
                )
                chunk_index += 1
                i += 1
            else:
                # 超长段落：用 RecursiveCharacterTextSplitter 兜底切分
                sub_chunks = _split_long_text(
                    block.content,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    token_len=token_len,
                )
                for sub_text in sub_chunks:
                    chunks.append(
                        StructuredChunk(
                            chunk_id=_make_chunk_id(),
                            index=chunk_index,
                            content=sub_text,
                            heading_path=list(block.heading_path),
                            block_type="paragraph",
                            page_number=block.page_number,
                            position_index=block.position_index,
                            parent_chunk_id=None,
                            is_summary=False,
                        )
                    )
                    chunk_index += 1
                i += 1
            continue

        # 其他类型（不应出现，防御性处理）
        i += 1

    logger.info(
        "结构感知切片完成: 输入 blocks=%d 输出 chunks=%d chunk_size=%d",
        len(blocks),
        len(chunks),
        chunk_size,
    )
    return chunks


def _make_chunk(
    chunk_index: int,
    blocks: list[StructuredBlock],
    block_type: str,
) -> StructuredChunk:
    """从一组 blocks 构建一个 StructuredChunk。"""
    # 内容拼接
    content_parts: list[str] = []
    for b in blocks:
        if b.block_type == "heading":
            # 标题加 # 前缀增强可读性
            level = len(b.heading_path) + 1
            content_parts.append(f"{'#' * level} {b.content}")
        else:
            content_parts.append(b.content)

    content = "\n\n".join(content_parts)

    # 元数据：
    # - heading_path 取最完整的（最长的），因为标题块的 heading_path 不含自身
    #   而紧跟的段落块含完整路径（包括标题自身）
    # - page_number / position_index 取首个 block 的
    first = blocks[0]
    best_heading_path = max(
        (b.heading_path for b in blocks), key=lambda hp: len(hp)
    )

    return StructuredChunk(
        chunk_id=_make_chunk_id(),
        index=chunk_index,
        content=content,
        heading_path=list(best_heading_path),
        block_type=block_type,
        page_number=first.page_number,
        position_index=first.position_index,
        parent_chunk_id=None,
        is_summary=False,
    )


def _split_paragraphs(
    blocks: list[StructuredBlock],
    chunk_size: int,
    chunk_overlap: int,
    token_len,
    start_index: int,
) -> list[StructuredChunk]:
    """把段落 blocks 组合/拆分成 chunks，控制每个 chunk 不超 chunk_size。"""
    chunks: list[StructuredChunk] = []
    current_blocks: list[StructuredBlock] = []
    current_tokens = 0
    chunk_index = start_index

    for block in blocks:
        block_tokens = token_len(block.content)

        if current_tokens + block_tokens > chunk_size and current_blocks:
            # 当前 chunk 已满，输出
            chunks.append(
                _make_chunk(
                    chunk_index=chunk_index,
                    blocks=current_blocks,
                    block_type="mixed" if len(current_blocks) > 1 else "paragraph",
                )
            )
            chunk_index += 1
            current_blocks = []
            current_tokens = 0

        if block_tokens <= chunk_size:
            current_blocks.append(block)
            current_tokens += block_tokens
        else:
            # 超长段落兜底切分
            if current_blocks:
                chunks.append(
                    _make_chunk(
                        chunk_index=chunk_index,
                        blocks=current_blocks,
                        block_type="mixed" if len(current_blocks) > 1 else "paragraph",
                    )
                )
                chunk_index += 1
                current_blocks = []
                current_tokens = 0

            sub_chunks = _split_long_text(
                block.content,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                token_len=token_len,
            )
            for sub_text in sub_chunks:
                chunks.append(
                    StructuredChunk(
                        chunk_id=_make_chunk_id(),
                        index=chunk_index,
                        content=sub_text,
                        heading_path=list(block.heading_path),
                        block_type="paragraph",
                        page_number=block.page_number,
                        position_index=block.position_index,
                        parent_chunk_id=None,
                        is_summary=False,
                    )
                )
                chunk_index += 1

    # 最后一批
    if current_blocks:
        chunks.append(
            _make_chunk(
                chunk_index=chunk_index,
                blocks=current_blocks,
                block_type="mixed" if len(current_blocks) > 1 else "paragraph",
            )
        )

    return chunks


def _split_long_text(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
    token_len,
) -> list[str]:
    """超长文本兜底切分（使用 LangChain RecursiveCharacterTextSplitter）。"""
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except ImportError:
        # 无 LangChain 时简单按字符切
        step = max(chunk_size * 2, 1000)  # 粗略估算
        overlap = max(chunk_overlap * 2, 100)
        result = []
        start = 0
        while start < len(text):
            end = min(start + step, len(text))
            result.append(text[start:end])
            start += step - overlap
        return result

    # 中文场景分隔符（与 V1.5 splitter.py 同款）
    separators = [
        "\n\n", "\n",
        "。", "！", "？", "；",
        ".", "!", "?", ";",
        "，", ",", " ", "",
    ]

    splitter = RecursiveCharacterTextSplitter(
        separators=separators,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=token_len,
        keep_separator=False,
    )

    return splitter.split_text(text)


__all__ = ["StructuredChunk", "split_structured_blocks"]
