"""V2.0 T1 阶段单测（智能文档处理验收）。

覆盖：
1. StructuredBlock 数据类
2. 结构感知解析（MD/TXT 样本；PDF/DOCX 需要 fixture 文件，留给集成测试）
3. StructuredChunk 数据类
4. 结构感知切片策略（代码块/表格不被切断、标题段落组合、超长段落兜底）
5. V2 入库管道步骤（解析→切片→noop→embed→milvus write→ner→bm25 noop）
6. V1.5 parse_document 兼容性零回归
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.ingest.parser import (
    ParseError,
    StructuredBlock,
    _detect_table,
    _parse_md_structured,
    _parse_md_heading_level,
    _parse_docx_heading_level,
    parse_document,
    parse_document_structured,
)
from app.ingest.structured_splitter import (
    StructuredChunk,
    split_structured_blocks,
    _make_chunk_id,
)


# ──────────────── 测试 fixture ────────────────


@pytest.fixture
def sample_md_file(tmp_path):
    """创建一个包含标题/段落/表格/代码块的 Markdown 文件。"""
    content = """# 第1章 台风概论

## 1.1 台风定义

台风是热带气旋的一种，在西北太平洋地区称为台风。

## 1.2 台风分类

| 等级 | 风速 (m/s) | 名称 |
| --- | --- | --- |
| 热带低压 | < 17.2 | TD |
| 热带风暴 | 17.2-24.4 | TS |

```python
def calculate_wind_speed(pressure):
    return 3.01 * (1010 - pressure) ** 0.5
```

这是正文段落，包含一些普通的文字描述。

- 项目1
- 项目2
- 项目3
"""
    f = tmp_path / "sample.md"
    f.write_text(content, encoding="utf-8")
    return f


@pytest.fixture
def sample_txt_file(tmp_path):
    """创建一个纯文本文件。"""
    content = "这是第一段文字。\n\n这是第二段文字，包含更多内容。\n\n这是第三段。"
    f = tmp_path / "sample.txt"
    f.write_text(content, encoding="utf-8")
    return f


# ════════════════════════════════════════════════════════════════
# 1. StructuredBlock 数据类
# ════════════════════════════════════════════════════════════════


class TestStructuredBlock:
    def test_creation(self):
        block = StructuredBlock(
            block_id="abc123",
            block_type="paragraph",
            heading_path=["第1章", "1.1 节"],
            content="测试内容",
            page_number=1,
            position_index=0,
        )
        assert block.block_type == "paragraph"
        assert block.heading_path == ["第1章", "1.1 节"]
        assert block.page_number == 1

    def test_frozen(self):
        block = StructuredBlock(
            block_id="abc",
            block_type="heading",
            heading_path=[],
            content="标题",
            page_number=None,
            position_index=0,
        )
        with pytest.raises(AttributeError):
            block.content = "修改"  # type: ignore

    def test_block_types(self):
        """所有合法 block_type 都能创建。"""
        for bt in ("paragraph", "heading", "table", "code", "list"):
            block = StructuredBlock(
                block_id="x",
                block_type=bt,  # type: ignore
                heading_path=[],
                content="",
                page_number=None,
                position_index=0,
            )
            assert block.block_type == bt


# ════════════════════════════════════════════════════════════════
# 2. 结构感知解析 — Markdown
# ════════════════════════════════════════════════════════════════


class TestMdStructuredParser:
    def test_parse_heading(self, sample_md_file):
        blocks = _parse_md_structured(sample_md_file)
        headings = [b for b in blocks if b.block_type == "heading"]
        # 至少有 h1 "第1章" 和 h2 "1.1 台风定义" / "1.2 台风分类"
        assert len(headings) >= 2

    def test_heading_path_hierarchy(self, sample_md_file):
        blocks = _parse_md_structured(sample_md_file)
        headings = [b for b in blocks if b.block_type == "heading"]
        # h1 "第1章" heading_path 为空
        h1 = headings[0]
        assert h1.content == "第1章 台风概论"
        assert h1.heading_path == []

        # h2 "1.1 台风定义" heading_path 含 h1
        h2_blocks = [h for h in headings if "1.1" in h.content]
        if h2_blocks:
            assert "第1章 台风概论" in h2_blocks[0].heading_path

    def test_code_block_preserved(self, sample_md_file):
        blocks = _parse_md_structured(sample_md_file)
        code_blocks = [b for b in blocks if b.block_type == "code"]
        assert len(code_blocks) >= 1
        assert "calculate_wind_speed" in code_blocks[0].content

    def test_table_detected(self, sample_md_file):
        blocks = _parse_md_structured(sample_md_file)
        table_blocks = [b for b in blocks if b.block_type == "table"]
        assert len(table_blocks) >= 1
        # 表格内容应含 markdown 格式的 | 分隔符
        assert "|" in table_blocks[0].content

    def test_paragraph_blocks(self, sample_md_file):
        blocks = _parse_md_structured(sample_md_file)
        paragraphs = [b for b in blocks if b.block_type == "paragraph"]
        assert len(paragraphs) >= 1

    def test_position_index_incremental(self, sample_md_file):
        blocks = _parse_md_structured(sample_md_file)
        for i, block in enumerate(blocks):
            assert block.position_index == i


class TestMdHeadingLevel:
    def test_h1(self):
        assert _parse_md_heading_level("h1") == 1

    def test_h2(self):
        assert _parse_md_heading_level("h2") == 2

    def test_h3(self):
        assert _parse_md_heading_level("h3") == 3

    def test_h4_capped_at_3(self):
        assert _parse_md_heading_level("h4") == 3

    def test_non_heading(self):
        assert _parse_md_heading_level("p") is None

    def test_div(self):
        assert _parse_md_heading_level("div") is None


# ════════════════════════════════════════════════════════════════
# 3. 结构感知解析 — TXT
# ════════════════════════════════════════════════════════════════


class TestTxtStructuredParser:
    def test_txt_produces_paragraph_blocks(self, sample_txt_file):
        blocks = parse_document_structured(sample_txt_file)
        assert len(blocks) >= 2
        assert all(b.block_type == "paragraph" for b in blocks)

    def test_txt_heading_path_empty(self, sample_txt_file):
        blocks = parse_document_structured(sample_txt_file)
        assert all(b.heading_path == [] for b in blocks)

    def test_txt_page_number_none(self, sample_txt_file):
        blocks = parse_document_structured(sample_txt_file)
        assert all(b.page_number is None for b in blocks)


# ════════════════════════════════════════════════════════════════
# 4. 辅助函数
# ════════════════════════════════════════════════════════════════


class TestDetectTable:
    def test_pipe_table(self):
        text = "| a | b |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |"
        assert _detect_table(text) is True

    def test_tab_table(self):
        text = "a\tb\tc\n1\t2\t3\n4\t5\t6"
        assert _detect_table(text) is True

    def test_plain_text(self):
        text = "这是普通文本，没有表格格式。"
        assert _detect_table(text) is False

    def test_single_pipe_line(self):
        text = "a | b"
        assert _detect_table(text) is False


class TestDocxHeadingLevel:
    def test_heading_1(self):
        assert _parse_docx_heading_level("heading 1") == 1

    def test_heading_2(self):
        assert _parse_docx_heading_level("heading 2") == 2

    def test_heading_3(self):
        assert _parse_docx_heading_level("heading 3") == 3

    def test_heading_4_capped(self):
        assert _parse_docx_heading_level("heading 4") == 3

    def test_chinese_heading(self):
        assert _parse_docx_heading_level("标题 1") == 1

    def test_normal_style(self):
        assert _parse_docx_heading_level("normal") is None


# ════════════════════════════════════════════════════════════════
# 5. StructuredChunk 数据类
# ════════════════════════════════════════════════════════════════


class TestStructuredChunk:
    def test_creation(self):
        chunk = StructuredChunk(
            chunk_id="abc",
            index=0,
            content="内容",
            heading_path=["第1章"],
            block_type="paragraph",
            page_number=1,
            position_index=0,
            parent_chunk_id=None,
            is_summary=False,
        )
        assert chunk.block_type == "paragraph"
        assert chunk.is_summary is False

    def test_mixed_block_type(self):
        chunk = StructuredChunk(
            chunk_id="x",
            index=0,
            content="",
            heading_path=[],
            block_type="mixed",
            page_number=None,
            position_index=0,
            parent_chunk_id=None,
            is_summary=False,
        )
        assert chunk.block_type == "mixed"


# ════════════════════════════════════════════════════════════════
# 6. 结构感知切片策略
# ════════════════════════════════════════════════════════════════


def _make_blocks(block_specs: list[dict]) -> list[StructuredBlock]:
    """快速构建 StructuredBlock 列表。"""
    blocks = []
    for i, spec in enumerate(block_specs):
        blocks.append(
            StructuredBlock(
                block_id=f"block_{i}",
                block_type=spec.get("type", "paragraph"),
                heading_path=spec.get("heading_path", []),
                content=spec.get("content", ""),
                page_number=spec.get("page_number"),
                position_index=i,
            )
        )
    return blocks


class TestStructuredSplit:
    def test_code_block_not_split(self):
        """代码块必须整块保留，不论多长。"""
        long_code = "print(i)\n" * 500  # 远超 chunk_size
        blocks = _make_blocks([{"type": "code", "content": long_code}])
        chunks = split_structured_blocks(blocks, chunk_size=100, chunk_overlap=0)
        assert len(chunks) == 1
        assert chunks[0].block_type == "code"
        assert long_code in chunks[0].content

    def test_table_not_split(self):
        """表格块必须整块保留。"""
        table_content = "| a | b |\n| --- | --- |\n" + "| 1 | 2 |\n" * 100
        blocks = _make_blocks([{"type": "table", "content": table_content}])
        chunks = split_structured_blocks(blocks, chunk_size=100, chunk_overlap=0)
        assert len(chunks) == 1
        assert chunks[0].block_type == "table"

    def test_heading_with_following_paragraph(self):
        """标题 + 紧随的段落应组合到一个 chunk。"""
        blocks = _make_blocks([
            {"type": "heading", "content": "第1章", "heading_path": []},
            {"type": "paragraph", "content": "这是第1章的正文内容。"},
        ])
        chunks = split_structured_blocks(blocks, chunk_size=1000, chunk_overlap=0)
        # 标题和段落应组合
        assert len(chunks) >= 1
        # 第一个 chunk 应包含标题和段落
        assert "第1章" in chunks[0].content

    def test_heading_path_preserved(self):
        """切片的 heading_path 应继承自 block。"""
        blocks = _make_blocks([
            {"type": "heading", "content": "标题1", "heading_path": []},
            {"type": "paragraph", "content": "内容1", "heading_path": ["标题1"]},
        ])
        chunks = split_structured_blocks(blocks, chunk_size=1000, chunk_overlap=0)
        # 段落 chunk 的 heading_path 应含 "标题1"
        para_chunks = [c for c in chunks if "内容1" in c.content]
        assert len(para_chunks) >= 1
        assert "标题1" in para_chunks[0].heading_path

    def test_empty_blocks_returns_empty(self):
        blocks: list[StructuredBlock] = []
        chunks = split_structured_blocks(blocks, chunk_size=512, chunk_overlap=64)
        assert chunks == []

    def test_invalid_chunk_size(self):
        blocks = _make_blocks([{"type": "paragraph", "content": "text"}])
        with pytest.raises(ValueError):
            split_structured_blocks(blocks, chunk_size=0, chunk_overlap=0)

    def test_invalid_chunk_overlap(self):
        blocks = _make_blocks([{"type": "paragraph", "content": "text"}])
        with pytest.raises(ValueError):
            split_structured_blocks(blocks, chunk_size=100, chunk_overlap=-1)

    def test_list_block_preserved(self):
        """列表块应整块保留。"""
        list_content = "- 项目1\n- 项目2\n- 项目3"
        blocks = _make_blocks([{"type": "list", "content": list_content}])
        chunks = split_structured_blocks(blocks, chunk_size=100, chunk_overlap=0)
        assert len(chunks) == 1
        assert chunks[0].block_type == "list"

    def test_position_index_from_first_block(self):
        """chunk 的 position_index 应取首个 block 的。"""
        blocks = _make_blocks([
            {"type": "paragraph", "content": "段落1"},
            {"type": "paragraph", "content": "段落2"},
        ])
        chunks = split_structured_blocks(blocks, chunk_size=1000, chunk_overlap=0)
        # 第一个 chunk 的 position_index 应为 0
        assert chunks[0].position_index == 0

    def test_page_number_preserved(self):
        """page_number 应从 block 继承。"""
        blocks = _make_blocks([
            {"type": "paragraph", "content": "内容", "page_number": 5},
        ])
        chunks = split_structured_blocks(blocks, chunk_size=1000, chunk_overlap=0)
        assert chunks[0].page_number == 5


# ════════════════════════════════════════════════════════════════
# 7. V2 入库管道步骤（mock 验证流程）
# ════════════════════════════════════════════════════════════════


class TestIngestPipelineSteps:
    """验证 V2 入库管道各步骤的逻辑（不连真服务）。"""

    def test_step_table_description_noop(self):
        """Step 4 noop 不报错。"""
        from app.tasks.ingest_task import _step_table_description_noop

        _step_table_description_noop([])  # 不抛异常即通过

    def test_step_summary_noop(self):
        """Step 5 noop 不报错。"""
        from app.tasks.ingest_task import _step_summary_noop

        _step_summary_noop([])

    def test_step_doc_metadata_noop(self):
        """Step 6 noop 不报错。"""
        from app.tasks.ingest_task import _step_doc_metadata_noop

        _step_doc_metadata_noop(MagicMock(), [])

    def test_step_bm25_auto(self):
        """Step 10 确认步骤不报错。"""
        from app.tasks.ingest_task import _step_bm25_auto

        _step_bm25_auto()  # 不抛异常即通过

    def test_progress_anchors_order(self):
        """V2 progress 锚点应严格递增。"""
        from app.tasks.ingest_task import (
            PROGRESS_START,
            PROGRESS_PARSED,
            PROGRESS_SPLIT,
            PROGRESS_TABLE_DESC,
            PROGRESS_SUMMARY,
            PROGRESS_DOC_META,
            PROGRESS_EMBEDDED,
            PROGRESS_MILVUS,
            PROGRESS_NER,
            PROGRESS_BM25,
            PROGRESS_DONE,
        )

        anchors = [
            PROGRESS_START,
            PROGRESS_PARSED,
            PROGRESS_SPLIT,
            PROGRESS_TABLE_DESC,
            PROGRESS_SUMMARY,
            PROGRESS_DOC_META,
            PROGRESS_EMBEDDED,
            PROGRESS_MILVUS,
            PROGRESS_NER,
            PROGRESS_BM25,
            PROGRESS_DONE,
        ]
        for i in range(len(anchors) - 1):
            assert anchors[i] < anchors[i + 1], (
                f"progress 锚点不是严格递增: {anchors[i]} >= {anchors[i+1]}"
            )

    def test_step_milvus_write_v2_row_format(self):
        """验证 V2 Milvus 写入行的格式（含新字段）。"""
        from app.tasks.ingest_task import _step_milvus_write_v2

        # mock resources
        mock_resources = MagicMock()
        mock_milvus = MagicMock()
        mock_milvus.has_collection.return_value = True
        mock_resources.milvus = mock_milvus

        # 构建 mock 数据
        mock_kb = MagicMock()
        mock_kb.id = uuid.uuid4()
        mock_kb.embedding_dim = 4096

        mock_file = MagicMock()
        mock_file.id = uuid.uuid4()
        mock_file.filename = "test.pdf"
        mock_file.mime_type = "application/pdf"

        chunks = [
            StructuredChunk(
                chunk_id="chunk_0",
                index=0,
                content="测试内容",
                heading_path=["第1章", "1.1 节"],
                block_type="paragraph",
                page_number=1,
                position_index=0,
                parent_chunk_id=None,
                is_summary=False,
            )
        ]
        vectors = [[0.1] * 4096]

        _step_milvus_write_v2(
            mock_resources,
            kb=mock_kb,
            file_record=mock_file,
            chunks=chunks,
            vectors=vectors,
        )

        # 验证 upsert 被调用
        assert mock_milvus.upsert.called
        # 取 upsert 传入的数据
        call_args = mock_milvus.upsert.call_args
        rows = call_args.kwargs.get("data") or call_args[1].get("data")

        assert len(rows) == 1
        row = rows[0]

        # V2 新增字段必须存在
        assert "heading_path" in row
        assert "block_type" in row
        assert "page_number" in row
        assert "position_index" in row
        assert "parent_chunk_id" in row
        assert "is_summary" in row
        # sparse_vector 不应在写入数据中（Milvus BM25 Function 自动从 content 生成）
        assert "sparse_vector" not in row

        # 验证值
        assert row["heading_path"] == ["第1章", "1.1 节"]
        assert row["block_type"] == "paragraph"
        assert row["page_number"] == 1
        assert row["position_index"] == 0
        assert row["is_summary"] is False


# ════════════════════════════════════════════════════════════════
# 8. V1.5 兼容性零回归
# ════════════════════════════════════════════════════════════════


class TestV15Compatibility:
    """V1.5 parse_document() 接口仍然可用。"""

    def test_parse_document_txt(self, sample_txt_file):
        text = parse_document(sample_txt_file)
        assert isinstance(text, str)
        assert "第一段" in text

    def test_parse_document_md(self, sample_md_file):
        text = parse_document(sample_md_file)
        assert isinstance(text, str)
        assert len(text) > 0

    def test_parse_document_nonexistent(self):
        with pytest.raises(ParseError, match="文件不存在"):
            parse_document("/nonexistent/file.txt")

    def test_parse_document_unsupported_ext(self, tmp_path):
        f = tmp_path / "test.xyz"
        f.write_text("content", encoding="utf-8")
        with pytest.raises(ParseError, match="不支持的文件扩展名"):
            parse_document(f)


import uuid  # noqa: E402 — 测试中需要
