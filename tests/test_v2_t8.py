"""Query 改写 + Query NER + 三层配置合并 单测。

覆盖矩阵：
- resolve_options 三层合并优先级（API > KB > settings）
- rewrite_query：none / hyde / multi_query / 软降级
- extract_query_entities：薄封装 + 超时 + 软失败
- anchor_to_graph：并发 + 字节截断 + 上限 + 部分失败容错
- _multi_query_search RRF 融合
- KB schema retrieval_config 字段
- 端到端 v2_query：HyDE / multi_query / Graph RAG / KB 配置覆盖

mock 策略：patch hybrid_search / litellm.acompletion / Tracer / Neo4j driver，
全部跑 mock，无需真 DB / Milvus / Neo4j。
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ════════════════════════════════════════════════════════════════
# 1. resolve_options 三层合并（HRE-06）
# ════════════════════════════════════════════════════════════════


class TestResolveOptions:
    def _settings_stub(self, **overrides):
        from app.core.config import get_settings

        s = get_settings()
        # 用真实 Settings 实例，必要时覆盖字段
        for k, v in overrides.items():
            object.__setattr__(s, k, v)
        return s

    def test_all_none_falls_back_to_settings(self):
        from app.rag.retrieval_config import resolve_options
        from app.schemas.v2.query import QueryOptions

        s = self._settings_stub()
        r = resolve_options(options=QueryOptions(), kb=None, settings=s)
        assert r.top_k == 5  # _FALLBACK_TOP_K
        assert r.bm25_enable is True  # settings 默认
        assert r.query_rewrite == "none"
        assert r.enable_graph_rag is True
        assert r.rrf_k == 60

    def test_kb_overrides_settings(self):
        from app.rag.retrieval_config import resolve_options
        from app.schemas.v2.query import QueryOptions

        s = self._settings_stub()
        kb = SimpleNamespace(
            retrieval_config={
                "top_k": 10,
                "enable_graph_rag": False,
                "query_rewrite": "hyde",
            }
        )
        r = resolve_options(options=QueryOptions(), kb=kb, settings=s)
        assert r.top_k == 10
        assert r.enable_graph_rag is False
        assert r.query_rewrite == "hyde"

    def test_api_overrides_kb(self):
        """API options 优先于 KB.retrieval_config。"""
        from app.rag.retrieval_config import resolve_options
        from app.schemas.v2.query import QueryOptions

        s = self._settings_stub()
        kb = SimpleNamespace(
            retrieval_config={"top_k": 10, "enable_graph_rag": False}
        )
        opts = QueryOptions(top_k=20, enable_graph_rag=True)
        r = resolve_options(options=opts, kb=kb, settings=s)
        assert r.top_k == 20
        assert r.enable_graph_rag is True

    def test_kb_none_uses_settings_only(self):
        from app.rag.retrieval_config import resolve_options
        from app.schemas.v2.query import QueryOptions

        s = self._settings_stub()
        r = resolve_options(options=QueryOptions(), kb=None, settings=s)
        assert r.top_k == 5

    def test_partial_kb_config_falls_back_per_field(self):
        """KB 只配了部分字段，未配字段回落 settings/默认。"""
        from app.rag.retrieval_config import resolve_options
        from app.schemas.v2.query import QueryOptions

        s = self._settings_stub()
        kb = SimpleNamespace(retrieval_config={"top_k": 7})  # 只配 top_k
        r = resolve_options(options=QueryOptions(), kb=kb, settings=s)
        assert r.top_k == 7
        # 其他字段回落 settings
        assert r.bm25_enable is True
        assert r.enable_graph_rag is True

    def test_invalid_query_rewrite_in_kb_raises_40011(self):
        from app.api.exceptions import BusinessError
        from app.rag.retrieval_config import resolve_options
        from app.schemas.v2.query import QueryOptions

        s = self._settings_stub()
        kb = SimpleNamespace(retrieval_config={"query_rewrite": "bogus"})
        with pytest.raises(BusinessError) as exc_info:
            resolve_options(options=QueryOptions(), kb=kb, settings=s)
        assert exc_info.value.code == 40011

    def test_invalid_query_rewrite_in_settings_raises_40011(self):
        """settings.query_rewrite_default 写错时也走 40011。"""
        from app.api.exceptions import BusinessError
        from app.rag.retrieval_config import resolve_options
        from app.schemas.v2.query import QueryOptions

        s = self._settings_stub(query_rewrite_default="invalid_strategy")
        with pytest.raises(BusinessError) as exc_info:
            resolve_options(options=QueryOptions(), kb=None, settings=s)
        assert exc_info.value.code == 40011

    def test_explicit_false_not_treated_as_unset(self):
        """API 显式传 enable_graph_rag=False 应该真的关掉，不能回落到 KB/settings。"""
        from app.rag.retrieval_config import resolve_options
        from app.schemas.v2.query import QueryOptions

        s = self._settings_stub()  # 默认 True
        opts = QueryOptions(enable_graph_rag=False)
        r = resolve_options(options=opts, kb=None, settings=s)
        assert r.enable_graph_rag is False


# ════════════════════════════════════════════════════════════════
# 2. rewrite_query（HRE-01）
# ════════════════════════════════════════════════════════════════


class TestRewriteQuery:
    @pytest.mark.asyncio
    async def test_none_strategy_zero_llm_call(self):
        from app.rag.query_rewriter import rewrite_query

        with patch("app.rag.query_rewriter.litellm.acompletion") as mock_acomp:
            r = await rewrite_query("什么是台风？", "none")
        assert r.rewritten_text is None
        assert r.sub_queries == []
        mock_acomp.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_query_returns_noop(self):
        from app.rag.query_rewriter import rewrite_query

        r = await rewrite_query("", "hyde")
        assert r.rewritten_text is None
        assert r.sub_queries == []

    def test_rewriter_kwargs_uses_unified_provider_prefix_resolution(self):
        """Query 改写器应复用统一前缀推断，Qwen/Qwen3 需补 openai/。"""
        from types import SimpleNamespace

        from app.rag.query_rewriter import _resolve_rewriter_kwargs

        with patch("app.rag.query_rewriter.get_settings") as mock_settings:
            mock_settings.return_value = SimpleNamespace(
                query_rewriter_model="Qwen/Qwen3-8B",
                litellm_model=None,
                litellm_api_base="https://api.siliconflow.cn/v1",
                litellm_api_key="sk-test",
                litellm_timeout=30.0,
                litellm_num_retries=1,
            )
            kwargs = _resolve_rewriter_kwargs(
                messages=[{"role": "user", "content": "台风"}]
            )
        assert kwargs["model"] == "openai/Qwen/Qwen3-8B"
        assert kwargs["api_base"] == "https://api.siliconflow.cn/v1"

    @pytest.mark.asyncio
    async def test_hyde_happy_path(self):
        from app.rag.query_rewriter import rewrite_query

        hypothetical = (
            "台风是热带洋面上发展起来的强烈热带气旋，中心附近最大风力达 12 级以上。"
            "通常发生在西北太平洋及南海一带。"
        )
        mock_resp = MagicMock()
        mock_resp.model_dump = lambda: {
            "choices": [{"message": {"content": hypothetical}}]
        }

        with patch(
            "app.rag.query_rewriter.litellm.acompletion",
            new=AsyncMock(return_value=mock_resp),
        ):
            r = await rewrite_query("什么是台风？", "hyde")

        assert r.rewritten_text == hypothetical
        assert r.sub_queries == []

    @pytest.mark.asyncio
    async def test_hyde_too_short_falls_back(self):
        """假设答案过短（< 10 字）软降级为 none。"""
        from app.rag.query_rewriter import rewrite_query

        mock_resp = MagicMock()
        mock_resp.model_dump = lambda: {"choices": [{"message": {"content": "短"}}]}
        with patch(
            "app.rag.query_rewriter.litellm.acompletion",
            new=AsyncMock(return_value=mock_resp),
        ):
            r = await rewrite_query("台风", "hyde")
        assert r.rewritten_text is None  # 软降级

    @pytest.mark.asyncio
    async def test_multi_query_parses_json(self):
        from app.rag.query_rewriter import rewrite_query

        sub_q = ["台风的形成机理", "台风的等级划分", "台风的影响范围"]
        mock_resp = MagicMock()
        mock_resp.model_dump = lambda: {
            "choices": [{"message": {"content": json.dumps({"sub_queries": sub_q})}}]
        }
        with patch(
            "app.rag.query_rewriter.litellm.acompletion",
            new=AsyncMock(return_value=mock_resp),
        ):
            r = await rewrite_query("什么是台风？", "multi_query")
        assert r.sub_queries == sub_q

    @pytest.mark.asyncio
    async def test_multi_query_strips_code_fence(self):
        """LLM 偶尔会用 ```json 围栏，必须能正确剥离。"""
        from app.rag.query_rewriter import rewrite_query

        content = '```json\n{"sub_queries": ["q1", "q2"]}\n```'
        mock_resp = MagicMock()
        mock_resp.model_dump = lambda: {"choices": [{"message": {"content": content}}]}
        with patch(
            "app.rag.query_rewriter.litellm.acompletion",
            new=AsyncMock(return_value=mock_resp),
        ):
            r = await rewrite_query("台风", "multi_query")
        assert r.sub_queries == ["q1", "q2"]

    @pytest.mark.asyncio
    async def test_multi_query_empty_falls_back(self):
        from app.rag.query_rewriter import rewrite_query

        mock_resp = MagicMock()
        mock_resp.model_dump = lambda: {
            "choices": [{"message": {"content": '{"sub_queries": []}'}}]
        }
        with patch(
            "app.rag.query_rewriter.litellm.acompletion",
            new=AsyncMock(return_value=mock_resp),
        ):
            r = await rewrite_query("台风", "multi_query")
        assert r.sub_queries == []
        assert r.rewritten_text is None

    @pytest.mark.asyncio
    async def test_llm_exception_soft_fail(self):
        from app.rag.query_rewriter import rewrite_query

        with patch(
            "app.rag.query_rewriter.litellm.acompletion",
            new=AsyncMock(side_effect=RuntimeError("LLM down")),
        ):
            r = await rewrite_query("台风", "hyde")
        # 软降级
        assert r.rewritten_text is None
        assert r.sub_queries == []

    @pytest.mark.asyncio
    async def test_timeout_soft_fail(self):
        """LLM 超时（asyncio.wait_for）→ 软降级返 noop。"""
        import asyncio

        from app.rag.query_rewriter import rewrite_query

        async def slow(*a, **kw):
            await asyncio.sleep(10)

        with patch(
            "app.rag.query_rewriter.litellm.acompletion", new=AsyncMock(side_effect=slow)
        ), patch("app.rag.query_rewriter.get_settings") as mock_get:
            # 缩短 timeout 加速测试
            mock_get.return_value = SimpleNamespace(
                query_rewriter_model="deepseek/deepseek-chat",
                litellm_model="deepseek/deepseek-chat",
                litellm_api_base=None,
                litellm_api_key=None,
                litellm_timeout=60.0,
                litellm_num_retries=0,
                multi_query_count=3,
                query_ner_timeout_s=0.1,
            )
            r = await rewrite_query("台风", "hyde")
        assert r.rewritten_text is None

    @pytest.mark.asyncio
    async def test_unknown_strategy_returns_noop(self):
        from app.rag.query_rewriter import rewrite_query

        r = await rewrite_query("台风", "future_strategy_xyz")
        assert r.rewritten_text is None
        assert r.sub_queries == []


# ════════════════════════════════════════════════════════════════
# 3. extract_query_entities（HRE-02）
# ════════════════════════════════════════════════════════════════


class TestExtractQueryEntities:
    @pytest.mark.asyncio
    async def test_happy_path_delegates_to_run_ner(self):
        from app.rag.query_ner import extract_query_entities

        entities = [{"name": "张三", "type": "PERSON"}]
        with patch(
            "app.rag.query_ner.run_ner", new=AsyncMock(return_value=entities)
        ) as mock_run:
            r = await extract_query_entities("张三的合同")
        assert r == entities
        mock_run.assert_awaited_once_with("张三的合同")

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        from app.rag.query_ner import extract_query_entities

        r = await extract_query_entities("")
        assert r == []
        r = await extract_query_entities("   ")
        assert r == []

    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self):
        import asyncio

        from app.rag.query_ner import extract_query_entities

        async def slow(*a, **kw):
            await asyncio.sleep(10)

        with patch("app.rag.query_ner.run_ner", new=AsyncMock(side_effect=slow)), patch(
            "app.rag.query_ner.get_settings"
        ) as mock_get:
            mock_get.return_value = SimpleNamespace(
                query_ner_timeout_s=0.05, graph_anchor_timeout_s=0.1
            )
            r = await extract_query_entities("张三")
        assert r == []

    @pytest.mark.asyncio
    async def test_run_ner_exception_returns_empty(self):
        from app.rag.query_ner import extract_query_entities

        with patch(
            "app.rag.query_ner.run_ner", new=AsyncMock(side_effect=RuntimeError("oops"))
        ):
            r = await extract_query_entities("张三")
        assert r == []


# ════════════════════════════════════════════════════════════════
# 4. anchor_to_graph（HRE-02）
# ════════════════════════════════════════════════════════════════


class TestAnchorToGraph:
    @pytest.mark.asyncio
    async def test_empty_entities_returns_empty(self):
        from app.rag.query_ner import anchor_to_graph

        r = await anchor_to_graph([], None)
        assert r == []

    @pytest.mark.asyncio
    async def test_concurrent_query_collects_all_neighbors(self):
        """多实体并发查 Neo4j，邻居全部收集。"""
        from app.rag.query_ner import anchor_to_graph

        async def fake_query(*, driver, entity_name, **kwargs):
            if entity_name == "张三":
                return [
                    {
                        "nodes_in_path": [
                            {"name": "张三", "type": "PERSON"},
                            {"name": "采购合同_2024", "type": "OTHER"},
                        ]
                    }
                ]
            if entity_name == "北京科技公司":
                return [
                    {
                        "nodes_in_path": [
                            {"name": "北京科技公司", "type": "ORG"},
                            {"name": "违约条款", "type": "OTHER"},
                        ]
                    }
                ]
            return []

        with patch(
            "app.rag.query_ner.execute_graph_query", new=AsyncMock(side_effect=fake_query)
        ), patch("app.rag.query_ner.get_neo4j_driver", return_value=MagicMock()):
            r = await anchor_to_graph(
                [
                    {"name": "张三", "type": "PERSON"},
                    {"name": "北京科技公司", "type": "ORG"},
                ],
                kb_ids=["kb-1"],
            )
        # 起点实体 + 邻居都被收集，去重后保持顺序
        assert "张三" in r
        assert "北京科技公司" in r
        assert "采购合同_2024" in r
        assert "违约条款" in r

    @pytest.mark.asyncio
    async def test_partial_failure_skipped_others_continue(self):
        from app.rag.query_ner import anchor_to_graph

        async def fake_query(*, driver, entity_name, **kwargs):
            if entity_name == "broken":
                raise RuntimeError("Neo4j down for this entity")
            return [{"nodes_in_path": [{"name": entity_name, "type": "OTHER"}, {"name": "邻居", "type": "OTHER"}]}]

        with patch(
            "app.rag.query_ner.execute_graph_query", new=AsyncMock(side_effect=fake_query)
        ), patch("app.rag.query_ner.get_neo4j_driver", return_value=MagicMock()):
            r = await anchor_to_graph(
                [
                    {"name": "broken", "type": "OTHER"},
                    {"name": "ok", "type": "OTHER"},
                ],
                kb_ids=None,
            )
        # broken 起点实体的 Neo4j 查询失败 → 该实体的 tags 全丢失（含起点自身）；
        # ok 的邻居正常收集（含 ok 起点 + 邻居）
        assert "ok" in r
        assert "邻居" in r
        # broken 起点实体不再保留——Query NER 抽出但图谱不存在的实体不会硬塞 tags，
        # 否则下游 ARRAY_CONTAINS_ANY 硬过滤会反向把召回归零
        assert "broken" not in r

    @pytest.mark.asyncio
    async def test_byte_truncation_for_long_chinese_names(self):
        """超过 64 字节的中文实体名（约 22 字以上）需按 UTF-8 字节截断。"""
        from app.rag.query_ner import _truncate_utf8

        long_name = "气" * 30  # 30 字 = 90 字节
        r = _truncate_utf8(long_name, 64)
        assert len(r.encode("utf-8")) <= 64
        # 截断结果是合法 UTF-8（不会半个字符）
        assert all(ord(c) > 0 for c in r)

    @pytest.mark.asyncio
    async def test_max_50_tags(self):
        from app.rag.query_ner import anchor_to_graph

        async def fake_query(*, driver, entity_name, **kwargs):
            # 单实体返 100 个邻居
            return [
                {"nodes_in_path": [{"name": f"邻居{i}", "type": "OTHER"} for i in range(100)]}
            ]

        with patch(
            "app.rag.query_ner.execute_graph_query", new=AsyncMock(side_effect=fake_query)
        ), patch("app.rag.query_ner.get_neo4j_driver", return_value=MagicMock()):
            r = await anchor_to_graph([{"name": "x", "type": "OTHER"}], None)
        assert len(r) <= 50

    @pytest.mark.asyncio
    async def test_neo4j_driver_unavailable_soft_fail(self):
        """get_neo4j_driver 抛异常时 → 软失败返 []，不阻断主链路。"""
        from app.rag.query_ner import anchor_to_graph

        with patch(
            "app.rag.query_ner.get_neo4j_driver", side_effect=RuntimeError("not init")
        ):
            r = await anchor_to_graph([{"name": "x", "type": "OTHER"}], None)
        assert r == []


# ════════════════════════════════════════════════════════════════
# 5. _multi_query_search RRF 融合
# ════════════════════════════════════════════════════════════════


class TestMultiQueryRRF:
    def _make_result(self, chunk_id: int, score: float = 0.8):
        from app.rag.hybrid_retriever import HybridSearchResult

        return HybridSearchResult(
            chunk_id=chunk_id,
            content=f"chunk-{chunk_id}",
            document_id=f"doc-{chunk_id}",
            score=score,
            metadata={"filename": "test.pdf"},
        )

    @pytest.mark.asyncio
    async def test_same_chunk_in_multiple_paths_score_accumulates(self):
        """同一 chunk 在多路命中，归一化 RRF 分数累加，排名靠前。"""
        from app.api.v2.endpoints.query import _multi_query_search

        # path 0 返回 [c=10, c=20]；path 1 返回 [c=10, c=30]
        # c=10 在两路都是 rank 1，归一化后 score=1.0
        # c=20 / c=30 各只在单路 rank 2，分数约为 0.492
        async def fake_search(query, top_k, entity_tags, **kwargs):
            if query == "q1":
                return [self._make_result(10), self._make_result(20)]
            if query == "q2":
                return [self._make_result(10), self._make_result(30)]
            return []

        with patch(
            "app.api.v2.endpoints.query.hybrid_search", new=AsyncMock(side_effect=fake_search)
        ):
            r = await _multi_query_search(
                queries=["q1", "q2"], top_k=3, entity_tags=None, rrf_k=60
            )

        ids_in_order = [item.chunk_id for item in r]
        assert ids_in_order[0] == 10  # 双路命中分数最高
        assert set(ids_in_order) == {10, 20, 30}
        assert r[0].score == pytest.approx(1.0)
        assert all(0.0 <= item.score <= 1.0 for item in r)

    @pytest.mark.asyncio
    async def test_one_path_failure_others_continue(self):
        from app.api.v2.endpoints.query import _multi_query_search

        async def fake_search(query, top_k, entity_tags, **kwargs):
            if query == "broken":
                raise RuntimeError("milvus blew up")
            return [self._make_result(1), self._make_result(2)]

        with patch(
            "app.api.v2.endpoints.query.hybrid_search", new=AsyncMock(side_effect=fake_search)
        ):
            r = await _multi_query_search(
                queries=["broken", "ok"], top_k=2, entity_tags=None, rrf_k=60
            )

        # broken 路抛错 → 仅 ok 路结果进入融合
        ids = {item.chunk_id for item in r}
        assert ids == {1, 2}

    @pytest.mark.asyncio
    async def test_top_k_truncates_after_rrf(self):
        from app.api.v2.endpoints.query import _multi_query_search

        async def fake_search(query, top_k, entity_tags, **kwargs):
            return [self._make_result(i) for i in range(1, 11)]  # 10 条

        with patch(
            "app.api.v2.endpoints.query.hybrid_search", new=AsyncMock(side_effect=fake_search)
        ):
            r = await _multi_query_search(
                queries=["q1", "q2"], top_k=3, entity_tags=None, rrf_k=60
            )
        assert len(r) == 3

    @pytest.mark.asyncio
    async def test_rrf_score_normalized_by_valid_query_count(self):
        """multi_query 子查询数量增加时，RRF 分数不应超过 1.0。"""
        from app.api.v2.endpoints.query import _multi_query_search

        async def fake_search(query, top_k, entity_tags, **kwargs):
            return [self._make_result(10), self._make_result(20)]

        with patch(
            "app.api.v2.endpoints.query.hybrid_search", new=AsyncMock(side_effect=fake_search)
        ):
            r = await _multi_query_search(
                queries=["q1", "q2", "q3", "q4"], top_k=2, entity_tags=None, rrf_k=60
            )
        assert r[0].chunk_id == 10
        assert r[0].score == pytest.approx(1.0)
        assert r[1].score < 1.0

    @pytest.mark.asyncio
    async def test_empty_paths_do_not_reduce_normalized_score(self):
        """空结果路径不计入归一化分母，避免稀释有效召回。"""
        from app.api.v2.endpoints.query import _multi_query_search

        async def fake_search(query, top_k, entity_tags, **kwargs):
            if query == "empty":
                return []
            return [self._make_result(10)]

        with patch(
            "app.api.v2.endpoints.query.hybrid_search", new=AsyncMock(side_effect=fake_search)
        ):
            r = await _multi_query_search(
                queries=["ok", "empty"], top_k=2, entity_tags=None, rrf_k=60
            )
        assert len(r) == 1
        assert r[0].score == pytest.approx(1.0)


# ════════════════════════════════════════════════════════════════
# 6. generate_answer LLM kwargs
# ════════════════════════════════════════════════════════════════


class TestGenerateAnswerLLM:
    @pytest.mark.asyncio
    async def test_query_generate_answer_uses_unified_provider_prefix_resolution(self):
        """/v2/query 生成答案应复用统一前缀推断。"""
        from types import SimpleNamespace

        from app.api.v2.endpoints.query import generate_answer

        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="答案"))]
        with patch("app.api.v2.endpoints.query.get_settings") as mock_settings, \
             patch("litellm.acompletion", new=AsyncMock(return_value=resp)) as mock_acomp:
            mock_settings.return_value = SimpleNamespace(
                litellm_model="Qwen/Qwen3-8B",
                litellm_api_key="sk-test",
                litellm_api_base="https://api.siliconflow.cn/v1",
                litellm_timeout=60.0,
                litellm_num_retries=0,
            )
            answer = await generate_answer(
                query="测试",
                context="上下文",
                session_id=None,
                db=MagicMock(),
            )
        assert answer == "答案"
        assert mock_acomp.call_args.kwargs["model"] == "openai/Qwen/Qwen3-8B"


# ════════════════════════════════════════════════════════════════
# 7. Schema 校验 + KB CRUD 暴露 retrieval_config
# ════════════════════════════════════════════════════════════════


class TestSchemas:
    def test_query_options_top_k_default_none(self):
        from app.schemas.v2.query import QueryOptions

        opts = QueryOptions()
        assert opts.top_k is None
        assert opts.query_rewrite is None
        assert opts.enable_graph_rag is None

    def test_kb_update_accepts_retrieval_config(self):
        from app.schemas.knowledge_base import KnowledgeBaseUpdateRequest

        req = KnowledgeBaseUpdateRequest(
            retrieval_config={"top_k": 10, "enable_graph_rag": False}
        )
        assert req.retrieval_config == {"top_k": 10, "enable_graph_rag": False}
        # name / description 可不传
        assert req.name is None

    def test_kb_update_at_least_one_field(self):
        from pydantic import ValidationError

        from app.schemas.knowledge_base import KnowledgeBaseUpdateRequest

        with pytest.raises(ValidationError):
            KnowledgeBaseUpdateRequest()

    def test_kb_update_retrieval_config_alone_ok(self):
        """只传 retrieval_config 也满足 at_least_one。"""
        from app.schemas.knowledge_base import KnowledgeBaseUpdateRequest

        req = KnowledgeBaseUpdateRequest(retrieval_config={})
        assert req.retrieval_config == {}

    def test_kb_detail_includes_retrieval_config(self):
        """KnowledgeBaseDetail.from_orm_kb 透传 retrieval_config。"""
        import datetime
        import uuid as _uuid

        from app.schemas.knowledge_base import KnowledgeBaseDetail

        fake_kb = SimpleNamespace(
            id=_uuid.uuid4(),
            name="test",
            description=None,
            embedding_dim=4096,
            chunk_size=512,
            chunk_overlap=64,
            status="active",
            file_count=0,
            chunk_count=0,
            retrieval_config={"top_k": 7},
            created_at=datetime.datetime.now(),
        )
        d = KnowledgeBaseDetail.from_orm_kb(fake_kb, entity_count=0)
        assert d.retrieval_config == {"top_k": 7}


# ════════════════════════════════════════════════════════════════
# 7. 端到端 v2_query 集成（HRE-01/02/06 全链路）
# ════════════════════════════════════════════════════════════════


def _make_tracer_mock():
    """构造一个能用作 async with + with step() 上下文的 Tracer mock。"""
    tracer = MagicMock()
    tracer.trace_id = "trace-t8"
    tracer.__aenter__ = AsyncMock(return_value=tracer)
    tracer.__aexit__ = AsyncMock(return_value=False)
    step = MagicMock()
    step.__enter__ = MagicMock(return_value=step)
    step.__exit__ = MagicMock(return_value=False)
    tracer.step = MagicMock(return_value=step)
    return tracer


class TestV2QueryE2E:
    @pytest.mark.asyncio
    async def test_hyde_strategy_uses_hypothetical_text_for_search(self):
        """HyDE：用假设答案替代原 query 做检索（hybrid_search 收到改写后的文本）。"""
        from app.api.v2.endpoints.query import v2_query
        from app.rag.hybrid_retriever import HybridSearchResult
        from app.rag.query_rewriter import RewriteResult
        from app.schemas.v2.query import QueryOptions, QueryRequest

        results = [
            HybridSearchResult(
                chunk_id=1, content="台风", document_id="d1", score=0.9,
                metadata={"filename": "f.pdf"},
            ),
        ]

        captured = {}

        async def capture_search(query, top_k, entity_tags, **kwargs):
            captured["query"] = query
            return results

        rew = RewriteResult(
            rewritten_text="台风是热带洋面发展起来的强烈热带气旋，主要发生在西北太平洋。",
            sub_queries=[],
        )

        llm_resp = MagicMock()
        llm_resp.choices = [MagicMock(message=MagicMock(content="答案 [1]"))]

        with patch(
            "app.api.v2.endpoints.query.hybrid_search",
            new=AsyncMock(side_effect=capture_search),
        ), patch(
            "app.api.v2.endpoints.query.Tracer", return_value=_make_tracer_mock()
        ), patch(
            "app.api.v2.endpoints.query.rewrite_query", new=AsyncMock(return_value=rew)
        ), patch(
            "app.api.v2.endpoints.query.extract_query_entities", new=AsyncMock(return_value=[])
        ), patch(
            "app.api.v2.endpoints.query.anchor_to_graph", new=AsyncMock(return_value=[])
        ), patch(
            "litellm.acompletion", new=AsyncMock(return_value=llm_resp)
        ):
            mock_db = AsyncMock()
            mock_db.get = AsyncMock(return_value=None)
            req = QueryRequest(query="什么是台风？", options=QueryOptions(query_rewrite="hyde"))
            resp = await v2_query(body=req, db=mock_db)

        # hybrid_search 收到的是改写后的假设答案，不是原 query
        assert captured["query"] == rew.rewritten_text
        # 响应里透出 rewritten_query
        assert resp.rewritten_query == rew.rewritten_text

    @pytest.mark.asyncio
    async def test_multi_query_strategy_paths_n_plus_one(self):
        """multi_query：N 个子查询 + 原 query，共 N+1 路并发检索。"""
        from app.api.v2.endpoints.query import v2_query
        from app.rag.hybrid_retriever import HybridSearchResult
        from app.rag.query_rewriter import RewriteResult
        from app.schemas.v2.query import QueryOptions, QueryRequest

        sub_q = ["子1", "子2", "子3"]
        rew = RewriteResult(rewritten_text=None, sub_queries=sub_q)

        call_log = []

        async def capture_search(query, top_k, entity_tags, **kwargs):
            call_log.append(query)
            return [
                HybridSearchResult(
                    chunk_id=hash(query) & 0xFFFF, content=query, document_id="d", score=0.9,
                    metadata={"filename": "f"},
                ),
            ]

        llm_resp = MagicMock()
        llm_resp.choices = [MagicMock(message=MagicMock(content="ans [1]"))]

        with patch(
            "app.api.v2.endpoints.query.hybrid_search",
            new=AsyncMock(side_effect=capture_search),
        ), patch(
            "app.api.v2.endpoints.query.Tracer", return_value=_make_tracer_mock()
        ), patch(
            "app.api.v2.endpoints.query.rewrite_query", new=AsyncMock(return_value=rew)
        ), patch(
            "app.api.v2.endpoints.query.extract_query_entities", new=AsyncMock(return_value=[])
        ), patch(
            "app.api.v2.endpoints.query.anchor_to_graph", new=AsyncMock(return_value=[])
        ), patch(
            "litellm.acompletion", new=AsyncMock(return_value=llm_resp)
        ):
            mock_db = AsyncMock()
            mock_db.get = AsyncMock(return_value=None)
            req = QueryRequest(query="原问题", options=QueryOptions(query_rewrite="multi_query"))
            resp = await v2_query(body=req, db=mock_db)

        # 子查询 + 原 query 都进了检索（N+1 路）
        assert set(call_log) == {"子1", "子2", "子3", "原问题"}
        assert resp.sub_queries == sub_q

    @pytest.mark.asyncio
    async def test_graph_rag_injects_entity_tags(self):
        """Graph RAG 命中实体时，entity_tags 注入 hybrid_search 调用。"""
        from app.api.v2.endpoints.query import v2_query
        from app.rag.hybrid_retriever import HybridSearchResult
        from app.rag.query_rewriter import RewriteResult
        from app.schemas.v2.query import QueryRequest

        captured = {}

        async def capture_search(query, top_k, entity_tags, **kwargs):
            captured["entity_tags"] = entity_tags
            return [
                HybridSearchResult(
                    chunk_id=1, content="x", document_id="d", score=0.9,
                    metadata={"filename": "f"},
                ),
            ]

        llm_resp = MagicMock()
        llm_resp.choices = [MagicMock(message=MagicMock(content="ans [1]"))]

        with patch(
            "app.api.v2.endpoints.query.hybrid_search",
            new=AsyncMock(side_effect=capture_search),
        ), patch(
            "app.api.v2.endpoints.query.Tracer", return_value=_make_tracer_mock()
        ), patch(
            "app.api.v2.endpoints.query.rewrite_query",
            new=AsyncMock(return_value=RewriteResult()),
        ), patch(
            "app.api.v2.endpoints.query.extract_query_entities",
            new=AsyncMock(return_value=[{"name": "张三", "type": "PERSON"}]),
        ), patch(
            "app.api.v2.endpoints.query.anchor_to_graph",
            new=AsyncMock(return_value=["张三", "采购合同"]),
        ), patch(
            "litellm.acompletion", new=AsyncMock(return_value=llm_resp)
        ):
            mock_db = AsyncMock()
            mock_db.get = AsyncMock(return_value=None)
            req = QueryRequest(query="张三", kb_ids=[uuid.uuid4()])
            resp = await v2_query(body=req, db=mock_db)

        assert captured["entity_tags"] == ["张三", "采购合同"]
        assert resp.ner_entities == [{"name": "张三", "type": "PERSON"}]
        assert resp.graph_anchored_tags == ["张三", "采购合同"]

    @pytest.mark.asyncio
    async def test_graph_rag_no_entities_short_circuits(self):
        """Query 无实体或实体不在图谱时，不传 entity_tags（短路）。"""
        from app.api.v2.endpoints.query import v2_query
        from app.rag.hybrid_retriever import HybridSearchResult
        from app.rag.query_rewriter import RewriteResult
        from app.schemas.v2.query import QueryRequest

        captured = {}

        async def capture_search(query, top_k, entity_tags, **kwargs):
            captured["entity_tags"] = entity_tags
            return [
                HybridSearchResult(
                    chunk_id=1, content="x", document_id="d", score=0.9,
                    metadata={"filename": "f"},
                ),
            ]

        llm_resp = MagicMock()
        llm_resp.choices = [MagicMock(message=MagicMock(content="ans [1]"))]

        with patch(
            "app.api.v2.endpoints.query.hybrid_search",
            new=AsyncMock(side_effect=capture_search),
        ), patch(
            "app.api.v2.endpoints.query.Tracer", return_value=_make_tracer_mock()
        ), patch(
            "app.api.v2.endpoints.query.rewrite_query",
            new=AsyncMock(return_value=RewriteResult()),
        ), patch(
            "app.api.v2.endpoints.query.extract_query_entities",
            new=AsyncMock(return_value=[]),  # 无实体
        ), patch(
            "app.api.v2.endpoints.query.anchor_to_graph", new=AsyncMock(return_value=[])
        ), patch(
            "litellm.acompletion", new=AsyncMock(return_value=llm_resp)
        ):
            mock_db = AsyncMock()
            mock_db.get = AsyncMock(return_value=None)
            req = QueryRequest(query="今天天气怎么样")
            resp = await v2_query(body=req, db=mock_db)

        assert captured["entity_tags"] is None  # 不传 → 短路
        assert resp.ner_entities is None
        assert resp.graph_anchored_tags is None

    @pytest.mark.asyncio
    async def test_kb_retrieval_config_overrides_settings(self):
        """KB.retrieval_config.enable_graph_rag=False 时，跳过 NER/锚定步骤。"""
        from app.api.v2.endpoints.query import v2_query
        from app.rag.hybrid_retriever import HybridSearchResult
        from app.rag.query_rewriter import RewriteResult
        from app.schemas.v2.query import QueryRequest

        async def fake_search(query, top_k, entity_tags, **kwargs):
            return [
                HybridSearchResult(
                    chunk_id=1, content="x", document_id="d", score=0.9,
                    metadata={"filename": "f"},
                ),
            ]

        ner_mock = AsyncMock(return_value=[])
        anchor_mock = AsyncMock(return_value=[])

        # 模拟 KB ORM 对象（带 retrieval_config）
        fake_kb = SimpleNamespace(retrieval_config={"enable_graph_rag": False})

        llm_resp = MagicMock()
        llm_resp.choices = [MagicMock(message=MagicMock(content="ans [1]"))]

        with patch(
            "app.api.v2.endpoints.query.hybrid_search",
            new=AsyncMock(side_effect=fake_search),
        ), patch(
            "app.api.v2.endpoints.query.Tracer", return_value=_make_tracer_mock()
        ), patch(
            "app.api.v2.endpoints.query.rewrite_query",
            new=AsyncMock(return_value=RewriteResult()),
        ), patch(
            "app.api.v2.endpoints.query.extract_query_entities", new=ner_mock
        ), patch(
            "app.api.v2.endpoints.query.anchor_to_graph", new=anchor_mock
        ), patch(
            "litellm.acompletion", new=AsyncMock(return_value=llm_resp)
        ):
            mock_db = AsyncMock()
            mock_db.get = AsyncMock(return_value=fake_kb)
            req = QueryRequest(query="张三", kb_ids=[uuid.uuid4()])
            await v2_query(body=req, db=mock_db)

        # KB 关闭 graph_rag → NER 不应被调用
        ner_mock.assert_not_awaited()
        anchor_mock.assert_not_awaited()
