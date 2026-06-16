"""V2.0 T7 · 表格描述 + 双层索引 + 文档元数据 单测。

覆盖矩阵：
- IDP-03 generate_table_descriptions：含表格 / 无表格 / 单张失败软跳过 / 超时 / 字节截断
- IDP-04 group_by_parent_heading：同父聚合 / 空 heading 单组 / 顺序稳定
- IDP-04 generate_coarse_chunks：happy / 关闭开关 / 单组失败 / 标题路径透传
- IDP-05 _parse_metadata：合法 / 字段缺失 / 围栏剥离 / 非法值过滤
- IDP-05 extract_doc_metadata：happy / 输入截断 / LLM 异常软失败
- _step_table_description：返回新 chunks 不修改原 fine / index 续号
- _step_dual_layer_index：parent_chunk_id 回填 / 粗 chunk index 续号 / 关闭开关短路
- _step_doc_metadata：写 PG / 软失败不抛
- _main 端到端：fine + td + coarse 都进 Milvus；NER 仅跑 fine；chunk_count 三类总和
- Schema：FileListItem / FileDetail 暴露 summary_brief & doc_metadata

mock 策略：patch litellm.acompletion / aembed_texts / Milvus / Neo4j；不连真服务。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingest.parser import StructuredBlock
from app.ingest.structured_splitter import StructuredChunk


# ════════════════════════════════════════════════════════════════
# 工具：构造 StructuredChunk
# ════════════════════════════════════════════════════════════════


def _chunk(
    *,
    index: int,
    content: str = "...",
    block_type: str = "paragraph",
    heading_path: list[str] | None = None,
    page_number: int | None = None,
    parent_chunk_id: str | None = None,
    is_summary: bool = False,
) -> StructuredChunk:
    return StructuredChunk(
        chunk_id=uuid.uuid4().hex,
        index=index,
        content=content,
        heading_path=heading_path or [],
        block_type=block_type,
        page_number=page_number,
        position_index=index,
        parent_chunk_id=parent_chunk_id,
        is_summary=is_summary,
    )


def _llm_resp(content: str) -> MagicMock:
    """构造 litellm.acompletion 的伪返回。"""
    resp = MagicMock()
    resp.model_dump = lambda: {"choices": [{"message": {"content": content}}]}
    return resp


# ════════════════════════════════════════════════════════════════
# 1. IDP-03 表格描述
# ════════════════════════════════════════════════════════════════


class TestTableDescription:
    @pytest.mark.asyncio
    async def test_no_table_returns_empty(self):
        from app.ingest.table_description import generate_table_descriptions

        chunks = [
            _chunk(index=0, block_type="paragraph"),
            _chunk(index=1, block_type="code"),
        ]
        with patch("app.ingest.table_description.litellm.acompletion") as mock_acomp:
            r = await generate_table_descriptions(chunks)
        assert r == []
        mock_acomp.assert_not_called()

    @pytest.mark.asyncio
    async def test_table_chunks_get_descriptions(self):
        from app.ingest.table_description import generate_table_descriptions

        chunks = [
            _chunk(index=0, block_type="paragraph"),
            _chunk(index=1, block_type="table", content="| A | B |\n|---|---|\n| 1 | 2 |"),
            _chunk(index=2, block_type="paragraph"),
            _chunk(index=3, block_type="table", content="| X | Y |"),
        ]
        with patch(
            "app.ingest.table_description.litellm.acompletion",
            new=AsyncMock(return_value=_llm_resp("销售额数据展示了 Q1 的趋势变化")),
        ):
            r = await generate_table_descriptions(chunks)

        # 2 张表都生成描述
        assert len(r) == 2
        assert {d.parent_index for d in r} == {1, 3}
        for d in r:
            assert "销售额" in d.description

    @pytest.mark.asyncio
    async def test_single_table_failure_soft_skip(self):
        """3 张表，第 2 张 LLM 失败 → 该表跳过，其他正常。"""
        from app.ingest.table_description import generate_table_descriptions

        chunks = [
            _chunk(index=i, block_type="table", content=f"table-{i}") for i in range(3)
        ]

        call_count = {"n": 0}

        async def fake_acomp(*args, **kwargs):
            call_count["n"] += 1
            # messages[1].content 是表格 markdown
            user_content = kwargs.get("messages", [{}, {}])[1].get("content", "")
            if "table-1" in user_content:
                raise RuntimeError("LLM throttled")
            return _llm_resp("一段表格描述内容详情")

        with patch(
            "app.ingest.table_description.litellm.acompletion",
            new=AsyncMock(side_effect=fake_acomp),
        ):
            r = await generate_table_descriptions(chunks)

        # 仅 2 张成功
        assert len(r) == 2
        assert {d.parent_index for d in r} == {0, 2}

    @pytest.mark.asyncio
    async def test_timeout_soft_skip(self):
        import asyncio

        from app.ingest.table_description import generate_table_descriptions

        chunks = [_chunk(index=0, block_type="table")]

        async def slow(*a, **kw):
            await asyncio.sleep(10)

        # 把 idp_llm_timeout_s 改小加速测试
        with patch(
            "app.ingest.table_description.litellm.acompletion", new=AsyncMock(side_effect=slow)
        ), patch("app.ingest.table_description.get_settings") as mock_get:
            mock_get.return_value = SimpleNamespace(
                idp_llm_model=None,
                litellm_model="deepseek/deepseek-chat",
                litellm_api_base=None,
                litellm_api_key=None,
                litellm_timeout=60.0,
                litellm_num_retries=0,
                idp_llm_timeout_s=0.05,
                idp_concurrency=2,
            )
            r = await generate_table_descriptions(chunks)
        assert r == []

    def test_truncate_utf8_chinese(self):
        from app.ingest.table_description import _truncate_utf8

        # 中文 3 字节/字；30 字 = 90 字节
        s = "测" * 30
        r = _truncate_utf8(s, 64)
        assert len(r.encode("utf-8")) <= 64
        # 是合法字符串，不会半个字
        assert isinstance(r, str)

    @pytest.mark.asyncio
    async def test_too_short_description_skipped(self):
        """描述过短（< 5 字）视为退化，跳过。"""
        from app.ingest.table_description import generate_table_descriptions

        chunks = [_chunk(index=0, block_type="table")]
        with patch(
            "app.ingest.table_description.litellm.acompletion",
            new=AsyncMock(return_value=_llm_resp("OK")),
        ):
            r = await generate_table_descriptions(chunks)
        assert r == []


# ════════════════════════════════════════════════════════════════
# 2. IDP-04 双层索引
# ════════════════════════════════════════════════════════════════


class TestGroupByParentHeading:
    def test_same_parent_grouped(self):
        from app.ingest.dual_layer import group_by_parent_heading

        chunks = [
            _chunk(index=0, heading_path=["第1章", "1.1节"]),
            _chunk(index=1, heading_path=["第1章", "1.2节"]),
            _chunk(index=2, heading_path=["第2章", "2.1节"]),
        ]
        groups = group_by_parent_heading(chunks)
        # 0+1 同父（第1章），2 自成一组
        assert len(groups) == 2
        assert sorted(groups[0]) == [0, 1]
        assert groups[1] == [2]

    def test_empty_heading_grouped_together(self):
        from app.ingest.dual_layer import group_by_parent_heading

        chunks = [
            _chunk(index=0, heading_path=[]),
            _chunk(index=1, heading_path=[]),
            _chunk(index=2, heading_path=["A"]),  # 单层 heading_path[:-1]=()
        ]
        groups = group_by_parent_heading(chunks)
        # 0+1+2 都 key=()，同组
        assert len(groups) == 1
        assert sorted(groups[0]) == [0, 1, 2]

    def test_order_preserved(self):
        from app.ingest.dual_layer import group_by_parent_heading

        chunks = [
            _chunk(index=0, heading_path=["A", "a1"]),
            _chunk(index=1, heading_path=["B", "b1"]),
            _chunk(index=2, heading_path=["A", "a2"]),
        ]
        groups = group_by_parent_heading(chunks)
        # A 组先出现，B 组后；保持出现顺序
        assert groups[0] == [0, 2]
        assert groups[1] == [1]

    def test_empty_chunks(self):
        from app.ingest.dual_layer import group_by_parent_heading

        assert group_by_parent_heading([]) == []


class TestGenerateCoarseChunks:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        from app.ingest.dual_layer import generate_coarse_chunks

        chunks = [
            _chunk(index=0, content="段落 A1", heading_path=["第1章", "1.1节"]),
            _chunk(index=1, content="段落 A2", heading_path=["第1章", "1.2节"]),
            _chunk(index=2, content="段落 B1", heading_path=["第2章", "2.1节"]),
        ]
        with patch(
            "app.ingest.dual_layer.litellm.acompletion",
            new=AsyncMock(return_value=_llm_resp("一段简明摘要内容")),
        ):
            r = await generate_coarse_chunks(chunks)
        # 2 个组 → 2 个粗 chunk
        assert len(r) == 2
        # 第一个组聚合 fine chunk 0、1
        assert sorted(r[0].parent_indices) == [0, 1]
        assert "摘要" in r[0].summary_text or "简明" in r[0].summary_text

    @pytest.mark.asyncio
    async def test_disabled_returns_empty(self):
        from app.ingest.dual_layer import generate_coarse_chunks

        chunks = [_chunk(index=0)]

        with patch("app.ingest.dual_layer.get_settings") as mock_get, patch(
            "app.ingest.dual_layer.litellm.acompletion"
        ) as mock_acomp:
            mock_get.return_value = SimpleNamespace(
                idp_dual_index_enable=False,
                idp_llm_model=None,
                litellm_model="deepseek/deepseek-chat",
                litellm_api_base=None,
                idp_llm_timeout_s=20.0,
                idp_concurrency=5,
            )
            r = await generate_coarse_chunks(chunks)
        assert r == []
        mock_acomp.assert_not_called()

    @pytest.mark.asyncio
    async def test_one_group_failure_soft_skip(self):
        from app.ingest.dual_layer import generate_coarse_chunks

        chunks = [
            _chunk(index=0, content="A 内容", heading_path=["A", "a1"]),
            _chunk(index=1, content="B 内容", heading_path=["B", "b1"]),
        ]

        async def fake(*a, **kw):
            user_content = kw.get("messages", [{}, {}])[1].get("content", "")
            if "B 内容" in user_content:
                raise RuntimeError("LLM down")
            return _llm_resp("A 组的简明摘要内容")

        with patch(
            "app.ingest.dual_layer.litellm.acompletion", new=AsyncMock(side_effect=fake)
        ):
            r = await generate_coarse_chunks(chunks)
        # 仅 A 组成功
        assert len(r) == 1
        assert r[0].parent_indices == [0]


# ════════════════════════════════════════════════════════════════
# 3. IDP-05 文档元数据
# ════════════════════════════════════════════════════════════════


class TestParseMetadata:
    def test_full_json_parses(self):
        from app.ingest.doc_metadata import _parse_metadata

        raw = json.dumps(
            {
                "doc_type": "合同",
                "doc_date": "2024-03",
                "language": "zh",
                "key_topics": ["违约金", "交付期限", "保密"],
                "summary_brief": "本合同规定了双方的权利义务。",
            },
            ensure_ascii=False,
        )
        m = _parse_metadata(raw)
        assert m.doc_type == "合同"
        assert m.doc_date == "2024-03"
        assert m.language == "zh"
        assert m.key_topics == ["违约金", "交付期限", "保密"]
        assert "权利义务" in m.summary_brief

    def test_invalid_doc_type_falls_to_none(self):
        from app.ingest.doc_metadata import _parse_metadata

        m = _parse_metadata(json.dumps({"doc_type": "随机类型"}))
        assert m.doc_type is None  # 非白名单值置空

    def test_strip_code_fence(self):
        from app.ingest.doc_metadata import _parse_metadata

        raw = '```json\n{"doc_type": "报告"}\n```'
        m = _parse_metadata(raw)
        assert m.doc_type == "报告"

    def test_invalid_json_returns_none(self):
        from app.ingest.doc_metadata import _parse_metadata

        assert _parse_metadata("不是 JSON") is None

    def test_key_topics_dedupe_and_clean(self):
        from app.ingest.doc_metadata import _parse_metadata

        raw = json.dumps(
            {
                "key_topics": ["a", "  a  ", "b", "", None, "c"],
            }
        )
        m = _parse_metadata(raw)
        # "a" 去重后保留一个；空字符串 / None 跳过
        assert m.key_topics == ["a", "b", "c"]

    def test_to_dict_excludes_summary_brief(self):
        from app.ingest.doc_metadata import DocMetadata

        m = DocMetadata(
            doc_type="合同", language="zh", summary_brief="摘要"
        )
        d = m.to_dict()
        assert d == {
            "doc_type": "合同",
            "doc_date": None,
            "language": "zh",
            "key_topics": [],
        }
        assert "summary_brief" not in d


class TestExtractDocMetadata:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        from app.ingest.doc_metadata import extract_doc_metadata

        blocks = [
            StructuredBlock(
                block_id="b0", block_type="heading", heading_path=[],
                content="测试合同", page_number=None, position_index=0,
            ),
            StructuredBlock(
                block_id="b1", block_type="paragraph", heading_path=["测试合同"],
                content="本合同由甲乙双方签订", page_number=None, position_index=1,
            ),
        ]
        meta_json = json.dumps(
            {
                "doc_type": "合同",
                "doc_date": "2024-01",
                "language": "zh",
                "key_topics": ["合同"],
                "summary_brief": "测试合同摘要",
            },
            ensure_ascii=False,
        )
        with patch(
            "app.ingest.doc_metadata.litellm.acompletion",
            new=AsyncMock(return_value=_llm_resp(meta_json)),
        ):
            m = await extract_doc_metadata(blocks)
        assert m.doc_type == "合同"
        assert m.summary_brief == "测试合同摘要"

    @pytest.mark.asyncio
    async def test_empty_blocks_returns_none(self):
        from app.ingest.doc_metadata import extract_doc_metadata

        assert await extract_doc_metadata([]) is None

    @pytest.mark.asyncio
    async def test_llm_failure_returns_none(self):
        from app.ingest.doc_metadata import extract_doc_metadata

        blocks = [
            StructuredBlock(
                block_id="b0", block_type="paragraph", heading_path=[],
                content="x", page_number=None, position_index=0,
            ),
        ]
        with patch(
            "app.ingest.doc_metadata.litellm.acompletion",
            new=AsyncMock(side_effect=RuntimeError("oops")),
        ):
            r = await extract_doc_metadata(blocks)
        assert r is None


# ════════════════════════════════════════════════════════════════
# 4. ingest_task 的 IDP 步骤接通
# ════════════════════════════════════════════════════════════════


class TestStepTableDescription:
    @pytest.mark.asyncio
    async def test_returns_new_chunks_with_correct_parent_id(self):
        """新 chunks 的 parent_chunk_id 是原表格 chunk 的 INT64 chunk_id 字符串。"""
        from app.tasks.ingest_task import _make_chunk_id_int, _step_table_description

        document_id = str(uuid.uuid4())
        fine_chunks = [
            _chunk(index=0, block_type="paragraph"),
            _chunk(index=1, block_type="table", content="| A | B |"),
        ]
        with patch(
            "app.ingest.table_description.litellm.acompletion",
            new=AsyncMock(return_value=_llm_resp("一段表格描述")),
        ):
            td_chunks = await _step_table_description(fine_chunks, document_id=document_id)

        assert len(td_chunks) == 1
        td = td_chunks[0]
        # index 续号
        assert td.index == len(fine_chunks)  # 2
        assert td.block_type == "table_description"
        # parent_chunk_id 指向原表格 chunk
        expected_parent = str(_make_chunk_id_int(document_id, 1))
        assert td.parent_chunk_id == expected_parent
        # heading_path / page_number 复用原表格 chunk
        assert td.is_summary is False

    @pytest.mark.asyncio
    async def test_no_table_returns_empty(self):
        from app.tasks.ingest_task import _step_table_description

        fine_chunks = [_chunk(index=0, block_type="paragraph")]
        with patch(
            "app.ingest.table_description.litellm.acompletion"
        ) as mock_acomp:
            r = await _step_table_description(fine_chunks, document_id="x")
        assert r == []
        mock_acomp.assert_not_called()


class TestStepDualLayerIndex:
    @pytest.mark.asyncio
    async def test_disabled_returns_unchanged(self):
        from app.tasks.ingest_task import _step_dual_layer_index

        fine_chunks = [_chunk(index=0)]

        with patch("app.ingest.dual_layer.get_settings") as mock_get:
            mock_get.return_value = SimpleNamespace(
                idp_dual_index_enable=False,
                idp_llm_model=None,
                litellm_model="x/y",
                litellm_api_base=None,
                idp_llm_timeout_s=20.0,
                idp_concurrency=5,
            )
            new_fine, coarse = await _step_dual_layer_index(
                fine_chunks, td_chunk_count=0, document_id="d"
            )
        # 关闭开关 → fine 不变，coarse 空
        assert new_fine is fine_chunks
        assert coarse == []

    @pytest.mark.asyncio
    async def test_parent_chunk_id_backfill(self):
        """fine_chunks 同一父级 → 粗 chunk 生成 → 子的 parent_chunk_id 都指向它。"""
        from app.tasks.ingest_task import (
            _make_chunk_id_int,
            _step_dual_layer_index,
        )

        document_id = str(uuid.uuid4())
        fine_chunks = [
            _chunk(index=0, content="A1", heading_path=["第1章", "1.1"]),
            _chunk(index=1, content="A2", heading_path=["第1章", "1.2"]),
        ]
        with patch(
            "app.ingest.dual_layer.litellm.acompletion",
            new=AsyncMock(return_value=_llm_resp("第1章的简明摘要内容很长")),
        ):
            new_fine, coarse = await _step_dual_layer_index(
                fine_chunks, td_chunk_count=0, document_id=document_id
            )

        assert len(coarse) == 1
        coarse_id_int = _make_chunk_id_int(document_id, len(fine_chunks))
        # 两个 fine chunk 都被回填为这个粗 chunk 的 INT64 字符串
        assert new_fine[0].parent_chunk_id == str(coarse_id_int)
        assert new_fine[1].parent_chunk_id == str(coarse_id_int)
        # 粗 chunk 自己 is_summary=True、parent_chunk_id=None
        assert coarse[0].is_summary is True
        assert coarse[0].parent_chunk_id is None
        # 粗 chunk index 续号（fine_chunks 数量起）
        assert coarse[0].index == len(fine_chunks)

    @pytest.mark.asyncio
    async def test_coarse_index_starts_after_td(self):
        """有 td_chunks 时，粗 chunk index 从 fine_count + td_count 起。"""
        from app.tasks.ingest_task import _step_dual_layer_index

        fine_chunks = [
            _chunk(index=0, heading_path=["A", "1"]),
        ]
        with patch(
            "app.ingest.dual_layer.litellm.acompletion",
            new=AsyncMock(return_value=_llm_resp("一段简明摘要内容长度大于五字")),
        ):
            _, coarse = await _step_dual_layer_index(
                fine_chunks, td_chunk_count=3, document_id="d"
            )
        # fine 1 个 + td 3 个 → 粗 chunk index 从 4 起
        assert coarse[0].index == 1 + 3


class TestStepDocMetadata:
    @pytest.mark.asyncio
    async def test_writes_pg_on_success(self):
        from app.tasks.ingest_task import _step_doc_metadata
        from contextlib import asynccontextmanager

        # mock resources.db()
        mock_session = MagicMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        @asynccontextmanager
        async def fake_db():
            yield mock_session

        mock_resources = MagicMock()
        mock_resources.db = fake_db

        # mock kb_file
        file_record = MagicMock()
        file_record.id = uuid.uuid4()

        blocks = [
            StructuredBlock(
                block_id="b0", block_type="paragraph", heading_path=[],
                content="文档内容", page_number=None, position_index=0,
            ),
        ]

        meta_json = json.dumps(
            {"doc_type": "报告", "summary_brief": "摘要"}, ensure_ascii=False
        )
        with patch(
            "app.ingest.doc_metadata.litellm.acompletion",
            new=AsyncMock(return_value=_llm_resp(meta_json)),
        ):
            meta = await _step_doc_metadata(
                mock_resources, file_record=file_record, blocks=blocks
            )

        assert meta is not None
        assert meta.doc_type == "报告"
        # PG UPDATE 已调用
        mock_session.execute.assert_awaited()
        mock_session.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_soft_fail_returns_none(self):
        """LLM 异常 → 返 None，不写 PG，不抛错。"""
        from app.tasks.ingest_task import _step_doc_metadata
        from contextlib import asynccontextmanager

        mock_session = MagicMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        @asynccontextmanager
        async def fake_db():
            yield mock_session

        mock_resources = MagicMock()
        mock_resources.db = fake_db

        file_record = MagicMock()
        file_record.id = uuid.uuid4()

        blocks = [
            StructuredBlock(
                block_id="b0", block_type="paragraph", heading_path=[],
                content="x", page_number=None, position_index=0,
            ),
        ]
        with patch(
            "app.ingest.doc_metadata.litellm.acompletion",
            new=AsyncMock(side_effect=RuntimeError("down")),
        ):
            r = await _step_doc_metadata(
                mock_resources, file_record=file_record, blocks=blocks
            )
        assert r is None
        # PG 不应被写
        mock_session.execute.assert_not_called()


# ════════════════════════════════════════════════════════════════
# 5. _main 端到端
# ════════════════════════════════════════════════════════════════


class TestMainEndToEnd:
    @pytest.mark.asyncio
    async def test_three_kinds_of_chunks_written(self, monkeypatch):
        """fine + td + coarse 三类 chunk 都进入 Milvus；NER 仅跑 fine。"""
        from contextlib import asynccontextmanager

        from app.tasks import ingest_task

        # mock blocks（含 1 张表）
        blocks = [
            StructuredBlock(
                block_id="b0", block_type="paragraph", heading_path=["第1章"],
                content="段 A 内容", page_number=None, position_index=0,
            ),
            StructuredBlock(
                block_id="b1", block_type="table", heading_path=["第1章"],
                content="| A | B |\n|---|---|\n| 1 | 2 |", page_number=None, position_index=1,
            ),
        ]
        monkeypatch.setattr(
            ingest_task, "parse_document_structured", MagicMock(return_value=blocks)
        )

        async def fake_embed(texts):
            return [[0.1] * 4096 for _ in texts]

        monkeypatch.setattr(ingest_task, "aembed_texts", fake_embed)

        ner_call_log = {"sizes": []}

        async def fake_ner(chunks):
            ner_call_log["sizes"].append(len(chunks))
            return [[] for _ in chunks]

        monkeypatch.setattr(ingest_task, "_step_ner", fake_ner)

        # mock T7 IDP 三步：返定值
        async def fake_table_desc(fine_chunks, *, document_id):
            # 给 1 张表生成 1 个 description chunk
            return [
                StructuredChunk(
                    chunk_id=uuid.uuid4().hex,
                    index=len(fine_chunks),
                    content="表格描述：A vs B",
                    heading_path=["第1章"],
                    block_type="table_description",
                    page_number=None,
                    position_index=1,
                    parent_chunk_id="9999",
                    is_summary=False,
                )
            ]

        async def fake_dual(fine_chunks, *, td_chunk_count, document_id):
            # 给 1 个粗 chunk
            coarse = StructuredChunk(
                chunk_id=uuid.uuid4().hex,
                index=len(fine_chunks) + td_chunk_count,
                content="粗粒度摘要",
                heading_path=["第1章"],
                block_type="paragraph",
                page_number=None,
                position_index=0,
                parent_chunk_id=None,
                is_summary=True,
            )
            return fine_chunks, [coarse]

        async def fake_doc_meta(resources, *, file_record, blocks):
            return None

        monkeypatch.setattr(ingest_task, "_step_table_description", fake_table_desc)
        monkeypatch.setattr(ingest_task, "_step_dual_layer_index", fake_dual)
        monkeypatch.setattr(ingest_task, "_step_doc_metadata", fake_doc_meta)

        # mock resources
        mock_milvus = MagicMock()
        mock_milvus.has_collection = MagicMock(return_value=True)
        mock_milvus.upsert = MagicMock()

        mock_neo4j = MagicMock()
        mock_session = MagicMock()
        mock_session.run = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_neo4j.session = MagicMock(return_value=mock_session)

        mock_db_session = MagicMock()
        mock_db_session.execute = AsyncMock()
        mock_db_session.commit = AsyncMock()

        mock_result = MagicMock()
        file_id = uuid.uuid4()
        kb_id = uuid.uuid4()

        from app.models.kb_file import KbFile
        from app.models.knowledge_base import KnowledgeBase

        file_record = KbFile(
            id=file_id,
            kb_id=kb_id,
            filename="t.txt",
            file_path="/tmp/t.txt",
            file_size=10,
            mime_type="text/plain",
            status="pending",
            progress=0,
            chunk_count=0,
            entity_count=0,
        )
        file_record.created_at = datetime.now(timezone.utc)
        kb = KnowledgeBase(
            id=kb_id, name="t", embedding_dim=4096, chunk_size=512, chunk_overlap=64,
            status="active", file_count=1, chunk_count=0,
        )
        mock_result.scalar_one_or_none = MagicMock(side_effect=[file_record, kb])
        mock_db_session.execute.return_value = mock_result

        @asynccontextmanager
        async def db_factory():
            yield mock_db_session

        mock_resources = MagicMock()
        mock_resources.milvus = mock_milvus
        mock_resources.neo4j = mock_neo4j
        mock_resources.db = db_factory

        @asynccontextmanager
        async def fake_resources():
            yield mock_resources

        monkeypatch.setattr(ingest_task, "task_resources", fake_resources)

        result = await ingest_task._main(str(file_id), str(kb_id))

        # fine（来自 splitter）+ td（1）+ coarse（1）
        # splitter 至少切出 1 chunk 段落 + 1 chunk 表格
        fine_count = result["fine_chunk_count"]
        assert fine_count >= 2
        assert result["table_description_count"] == 1
        assert result["coarse_chunk_count"] == 1
        assert result["chunk_count"] == fine_count + 1 + 1

        # NER 仅跑 fine
        assert ner_call_log["sizes"] == [fine_count]

        # block_types 三类都有
        block_types = set(result["block_types"])
        assert "table_description" in block_types
        # 粗 chunk 用 paragraph type；fine 中已含 paragraph/table

        # Milvus.upsert 写入条数 = chunk_count
        all_rows: list[dict] = []
        for c in mock_milvus.upsert.call_args_list:
            all_rows.extend(c.kwargs["data"])
        assert len(all_rows) == result["chunk_count"]

        # 至少有一条 is_summary=True
        assert any(r["is_summary"] for r in all_rows)
        # 至少有一条 block_type=table_description
        assert any(r["block_type"] == "table_description" for r in all_rows)


# ════════════════════════════════════════════════════════════════
# 6. Schema 暴露
# ════════════════════════════════════════════════════════════════


class TestSchemas:
    def test_file_list_item_summary_brief(self):
        from app.schemas.kb_file import FileListItem

        # 模拟一个 ORM 对象
        fake_orm = SimpleNamespace(
            id=uuid.uuid4(),
            filename="t.pdf",
            file_size=10,
            mime_type="application/pdf",
            status="completed",
            progress=100,
            chunk_count=5,
            summary_brief="这是文档摘要",
            created_at=datetime.now(timezone.utc),
            completed_at=None,
        )
        item = FileListItem.model_validate(fake_orm)
        assert item.summary_brief == "这是文档摘要"

    def test_file_detail_doc_metadata(self):
        from app.schemas.kb_file import FileDetail

        fake_orm = SimpleNamespace(
            id=uuid.uuid4(),
            kb_id=uuid.uuid4(),
            filename="t.pdf",
            file_size=10,
            mime_type="application/pdf",
            status="completed",
            progress=100,
            chunk_count=5,
            entity_count=3,
            summary_brief="摘要",
            doc_metadata={
                "doc_type": "合同",
                "doc_date": "2024-03",
                "language": "zh",
                "key_topics": ["违约金"],
            },
            error_message=None,
            celery_task_id=None,
            created_at=datetime.now(timezone.utc),
            completed_at=None,
        )
        d = FileDetail.model_validate(fake_orm)
        assert d.doc_metadata["doc_type"] == "合同"
        assert d.summary_brief == "摘要"

    def test_file_list_item_summary_brief_optional(self):
        """summary_brief=None 也能正常构造（向后兼容老数据）。"""
        from app.schemas.kb_file import FileListItem

        fake_orm = SimpleNamespace(
            id=uuid.uuid4(),
            filename="t.pdf",
            file_size=10,
            mime_type="application/pdf",
            status="completed",
            progress=100,
            chunk_count=5,
            summary_brief=None,
            created_at=datetime.now(timezone.utc),
            completed_at=None,
        )
        item = FileListItem.model_validate(fake_orm)
        assert item.summary_brief is None
