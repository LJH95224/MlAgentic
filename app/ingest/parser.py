"""文档解析层（V1.5 PRD §3.3 FILE-01 / §6.1）。

支持格式（V1.5 第一版）：
- .pdf  → PyMuPDF (fitz) 逐页 page.get_text()；空文本页 warning 跳过
- .docx → python-docx 提取正文段落 + 表格文本；忽略页眉脚 / 图片
- .md   → markdown-it-py 渲染为 token 后取纯文本（剥语法符号）
- .txt  → 内置 open(encoding="utf-8")；UnicodeDecodeError → ParseError

PRD §6.1 中的 .doc 推迟到下一迭代（LibreOffice 在 Windows / CI 踩坑成本高）。

S3.7 决策（详见 v1.5_dev_plan.md）：
- 文件格式以**扩展名为主**判断分发器；MIME 仅做二次校验（不匹配 warning 不抛）
- 这是因为 Windows 上 .md 经常被识别为 application/octet-stream，硬卡 MIME 会破坏体验
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


# ──────────────── 公共类型 ────────────────


# V1.5 第一版支持的扩展名（小写、含点）
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".docx", ".md", ".txt"})

# 各扩展名期望的 MIME 集合（用于二次校验，不匹配仅 warning）
# 一个扩展名可能对应多个常见 MIME（不同浏览器 / 操作系统填的不一样）
EXPECTED_MIMES: dict[str, frozenset[str]] = {
    ".pdf": frozenset({"application/pdf"}),
    ".docx": frozenset(
        {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    ),
    ".md": frozenset({"text/markdown", "text/x-markdown", "text/plain"}),
    ".txt": frozenset({"text/plain"}),
}


class ParseError(Exception):
    """解析失败的统一异常；上层 Celery 任务捕获后翻译为 status=failed + error_message。"""


# ──────────────── 解析器实现 ────────────────


def _parse_pdf(path: Path) -> str:
    """逐页 PDF → 拼接纯文本。

    扫描页（图片型 PDF）text 为空时 warning 跳过，不中断（PRD §6.1）。
    需要 pymupdf；模块级 import 仅当 _parse_pdf 真被调用时才尝试，
    单测里可以 mock _PARSERS 字典绕过依赖。
    """
    try:
        import fitz  # pymupdf
    except ImportError as e:
        raise ParseError("解析 PDF 需要 pymupdf 库") from e

    parts: list[str] = []
    try:
        doc = fitz.open(str(path))
    except Exception as e:
        raise ParseError(f"PDF 打开失败：{e}") from e

    try:
        empty_pages: list[int] = []
        for page_idx, page in enumerate(doc):
            text = page.get_text() or ""
            if not text.strip():
                empty_pages.append(page_idx + 1)
                continue
            parts.append(text)
        if empty_pages:
            logger.warning(
                "PDF 解析: %d 页无文本(可能是扫描页)已跳过 - 页码=%s",
                len(empty_pages),
                empty_pages,
            )
    finally:
        doc.close()

    return "\n\n".join(parts)


def _parse_docx(path: Path) -> str:
    """python-docx 提取段落 + 表格文本；忽略页眉脚 / 图片。"""
    try:
        from docx import Document
    except ImportError as e:
        raise ParseError("解析 docx 需要 python-docx 库") from e

    try:
        doc = Document(str(path))
    except Exception as e:
        raise ParseError(f"docx 打开失败：{e}") from e

    parts: list[str] = []
    # 正文段落
    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text)
    # 表格（每个 cell 当独立段落处理；跨行/合并单元格按显示顺序展开）
    for table in doc.tables:
        for row in table.rows:
            row_texts = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if row_texts:
                # 同一行多个单元格用 | 分隔，便于阅读
                parts.append(" | ".join(row_texts))

    return "\n\n".join(parts)


def _parse_md(path: Path) -> str:
    """Markdown 剥语法符号后取纯文本。

    markdown-it-py 把 .md 解析成 token 流；这里遍历 token，拼接所有 `text` /
    `inline` 节点的文本内容，跳过纯结构性 token（如 fence 围栏标记本身）。
    代码块 / 行内代码的内容保留（它们对 RAG 检索价值高）。
    """
    try:
        from markdown_it import MarkdownIt
    except ImportError as e:
        raise ParseError("解析 markdown 需要 markdown-it-py 库") from e

    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise ParseError(f"markdown 文件非 UTF-8 编码：{e}") from e

    md = MarkdownIt()
    tokens = md.parse(raw)

    parts: list[str] = []

    def _walk(toks) -> None:
        for tok in toks:
            # inline token 的 content 是当前行的合并文本；children 才是细粒度
            # 走 children 可以避免内层节点重复采集
            if tok.type == "inline" and tok.children:
                _walk(tok.children)
                continue
            # text / code_inline / code_block / fence 的 content 保留
            if tok.type in ("text", "code_inline", "code_block", "fence"):
                if tok.content:
                    parts.append(tok.content)
            # 块级换行（段落 / 标题 / 列表项之间）
            if tok.type in (
                "paragraph_close",
                "heading_close",
                "list_item_close",
                "fence",
                "code_block",
            ):
                parts.append("\n")

    _walk(tokens)

    # 合并空行；中文常见 全角空格也 strip 掉
    joined = "".join(parts)
    # 去掉超过 2 个的连续换行
    while "\n\n\n" in joined:
        joined = joined.replace("\n\n\n", "\n\n")
    return joined.strip()


def _parse_txt(path: Path) -> str:
    """纯文本读取；仅 UTF-8。其它编码 → ParseError（上层 → 415）。"""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise ParseError(f"txt 文件非 UTF-8 编码：{e}") from e


# ──────────────── 分发表 ────────────────


_PARSERS: dict[str, Callable[[Path], str]] = {
    ".pdf": _parse_pdf,
    ".docx": _parse_docx,
    ".md": _parse_md,
    ".txt": _parse_txt,
}


# ──────────────── 对外入口 ────────────────


def is_supported_filename(filename: str) -> bool:
    """按扩展名判断是否支持（S3.7 决策：扩展名为主）。"""
    return Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS


def check_mime_compatibility(filename: str, declared_mime: str | None) -> bool:
    """二次校验：检查声明的 MIME 是否与扩展名匹配。

    不匹配仅返回 False，由调用方决定 warning 还是放行（PRD S3.7 决策：
    扩展名合法时即使 MIME 不匹配也放行，仅记 warning）。
    """
    if not declared_mime:
        return True  # 没声明 MIME 视为放行（curl / 部分客户端不带 content-type）
    ext = Path(filename).suffix.lower()
    expected = EXPECTED_MIMES.get(ext, frozenset())
    return declared_mime.lower() in expected


def parse_document(path: str | Path, *, filename: str | None = None) -> str:
    """根据扩展名分发到具体解析器，返回纯文本。

    Args:
        path: 磁盘路径
        filename: 原始文件名（用来取扩展名）；默认从 path 取

    Returns:
        解析后的纯文本（可能含换行；不做切片）

    Raises:
        ParseError: 文件不存在 / 格式不支持 / 解析失败
    """
    p = Path(path)
    if not p.exists():
        raise ParseError(f"文件不存在: {p}")

    ext = Path(filename or p.name).suffix.lower()
    parser = _PARSERS.get(ext)
    if parser is None:
        raise ParseError(
            f"不支持的文件扩展名: {ext}（当前支持 {sorted(SUPPORTED_EXTENSIONS)}）"
        )

    logger.info("解析文件: name=%s ext=%s path=%s", filename or p.name, ext, p)
    text = parser(p)
    logger.info(
        "解析完成: name=%s 文本长度=%d 字符", filename or p.name, len(text)
    )
    return text


__all__ = [
    "SUPPORTED_EXTENSIONS",
    "EXPECTED_MIMES",
    "ParseError",
    "is_supported_filename",
    "check_mime_compatibility",
    "parse_document",
]
