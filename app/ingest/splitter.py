"""文本切片层（V1.5 PRD §3.3 FILE-01 / §6.2）。

实现要点：
- 包装 LangChain `RecursiveCharacterTextSplitter`
- 长度单位：**Token 数**（用 tiktoken `cl100k_base` 估算，中文场景 ≈ 1 token / 1.5~2 字）
- 分隔符优先级（PRD §6.2）：段落 → 句子 → 词 → 字符
- 中文场景额外加 "。" "！" "？" "；" 等分隔符，让切片在句末优雅断开
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# 切片分隔符优先级（从高到低）
# 第一组：双换行 = 段落边界，最优先
# 中间组：中文 / 英文句末标点 + 单换行
# 最后：词 / 字符兜底
_SEPARATORS: list[str] = [
    "\n\n",        # 段落
    "\n",          # 行
    "。", "！", "？", "；",  # 中文句末
    ".", "!", "?", ";",       # 英文句末
    "，", ",",     # 逗号
    " ",           # 词分隔
    "",            # 字符兜底
]


@dataclass(frozen=True)
class Chunk:
    """切片产物。

    chunk_index 从 0 开始；用于生成稳定的 chunk_id：
        chunk_id = hash(document_id + chunk_index) & 0x7fff_ffff_ffff_ffff
    """

    index: int
    text: str


# ──────────────── tiktoken 长度函数 ────────────────


def _build_token_len_fn():
    """构造 tiktoken 长度函数；导入失败时退化为 len(text)。

    生产场景必须用 tiktoken（中文 1 字 ≈ 0.5~0.7 token，用 len 会切得太小）；
    单测 / 无 tiktoken 环境退化到 len 以保证可跑。
    """
    try:
        import tiktoken
    except ImportError:
        logger.warning("tiktoken 未安装，切片长度退化为 len(text)；生产环境必须装")
        return len

    # cl100k_base 是 GPT-3.5/4 / 大部分 OpenAI 兼容模型的默认 tokenizer
    encoding = tiktoken.get_encoding("cl100k_base")

    def _len(text: str) -> int:
        return len(encoding.encode(text))

    return _len


# 模块级缓存：tiktoken 加载有几百 ms 开销，每次切片都构造太浪费
_TOKEN_LEN_FN = None


def _get_token_len_fn():
    global _TOKEN_LEN_FN
    if _TOKEN_LEN_FN is None:
        _TOKEN_LEN_FN = _build_token_len_fn()
    return _TOKEN_LEN_FN


# ──────────────── 对外入口 ────────────────


def split_text(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> list[Chunk]:
    """把纯文本切成 Chunk 列表。

    Args:
        text: parser 输出的纯文本
        chunk_size: 单个切片最大 token 数（KB 配置传入）
        chunk_overlap: 相邻切片重叠 token 数

    Returns:
        Chunk 列表（按出现顺序，index 从 0）；空文本 → 返回 []

    Raises:
        ValueError: chunk_size <= 0 或 chunk_overlap < 0 或 overlap >= size
    """
    if chunk_size <= 0:
        raise ValueError(f"chunk_size 必须 > 0，得到 {chunk_size}")
    if chunk_overlap < 0:
        raise ValueError(f"chunk_overlap 必须 >= 0，得到 {chunk_overlap}")
    if chunk_overlap >= chunk_size:
        raise ValueError(
            f"chunk_overlap ({chunk_overlap}) 必须 < chunk_size ({chunk_size})"
        )

    text = (text or "").strip()
    if not text:
        return []

    # 局部导入：langchain-text-splitters 模块加载较重，无 ingest 任务时不必加载
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except ImportError as e:
        raise RuntimeError(
            "切片需要 langchain-text-splitters 库（V1.5 requirements 已声明）"
        ) from e

    splitter = RecursiveCharacterTextSplitter(
        separators=_SEPARATORS,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=_get_token_len_fn(),
        keep_separator=False,
    )

    pieces = splitter.split_text(text)
    chunks = [Chunk(index=i, text=p) for i, p in enumerate(pieces) if p.strip()]
    logger.info(
        "文本切片完成: 输入字符=%d 输出切片=%d size=%d overlap=%d",
        len(text),
        len(chunks),
        chunk_size,
        chunk_overlap,
    )
    return chunks


__all__ = ["Chunk", "split_text"]
