"""V2.0 Citation 模块（CHC-01/02）。

核心函数：
- build_context_with_citation(chunks)：构建带 [1][2] 引用标记的 context
- parse_citations(answer_text, chunks)：从 LLM 输出解析引用，映射回 chunks

设计要点：
- 每个检索结果分配唯一编号 [1] [2] [3]...
- context 格式含文档名 + 页码，方便溯源
- 解析用正则提取 [\\d+]，去重后映射回 chunks
- 未引用的检索结果不出现在 source_citations 中
"""

from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)


def build_context_with_citation(chunks: list[dict]) -> str:
    """构建带引用标记的 context 文本（CHC-01）。

    输入 chunks 格式：
        [{"document_name": "xxx.pdf", "page_number": 3, "content": "..."}, ...]

    输出格式：
        [1] 来源：xxx.pdf（第3页）
        内容：...

        [2] 来源：yyy.docx
        内容：...

    Args:
        chunks: 检索结果列表

    Returns:
        带 [N] 引用标记的 context 文本
    """
    if not chunks:
        return ""

    parts: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        doc_name = chunk.get("document_name", "未知文档")
        page = chunk.get("page_number")
        content = chunk.get("content", "")

        # 来源行
        if page is not None:
            source_line = f"[{i}] 来源：{doc_name}（第{page}页）"
        else:
            source_line = f"[{i}] 来源：{doc_name}"

        parts.append(f"{source_line}\n内容：{content}")

    return "\n\n".join(parts)


def build_citation_system_prompt() -> str:
    """构建引用规则的 system prompt 片段（注入到 LLM 指令中）。"""
    return (
        "回答时必须使用引用标记，格式为 [1] [2] [3] 等，对应检索结果中的编号。"
        "每个事实性陈述都应标注来源引用。"
        "引用编号放在相关语句的末尾，如“台风是热带气旋[1]”。"
        "不要编造引用编号，只使用检索结果中出现的编号。"
    )


def parse_citations(
    answer_text: str,
    chunks: list[dict],
) -> list[dict]:
    """从 LLM 输出中解析引用标记，映射回 chunks（CHC-02）。

    用正则 \\[(\\d+)\\] 抽出引用编号 → 去重 → 映射回 chunks。
    返回 source_citations 列表，每个元素含：
    - chunk_id / document_name / page_number / heading_path / snippet / rerank_score

    Args:
        answer_text: LLM 生成的答案文本
        chunks: 检索结果列表（与 build_context_with_citation 的输入一致）

    Returns:
        source_citations 列表（去重后，仅含 LLM 实际引用的 chunks）
    """
    if not answer_text or not chunks:
        return []

    # 提取所有 [N] 标记
    refs = re.findall(r"\[(\d+)\]", answer_text)
    if not refs:
        return []

    # 去重 + 转为整数索引
    seen: set[int] = set()
    cited_indices: list[int] = []
    for ref in refs:
        idx = int(ref)
        if idx not in seen and 1 <= idx <= len(chunks):
            seen.add(idx)
            cited_indices.append(idx)

    # 映射回 chunks
    citations: list[dict] = []
    for idx in cited_indices:
        chunk = chunks[idx - 1]  # [1] 对应 index 0

        citation = {
            "chunk_id": chunk.get("chunk_id"),
            "document_name": chunk.get("document_name", "未知文档"),
            "page_number": chunk.get("page_number"),
            "heading_path": chunk.get("heading_path") or [],
            "snippet": chunk.get("content", "")[:200],  # 截取前 200 字符作为摘要
            "rerank_score": chunk.get("rerank_score"),
        }
        citations.append(citation)

    return citations


__all__ = [
    "build_context_with_citation",
    "build_citation_system_prompt",
    "parse_citations",
]
