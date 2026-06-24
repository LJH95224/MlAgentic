"""置信度评分 + 答案自检 单测。

覆盖矩阵：
- CHC-03 compute_confidence：高分 / penalty / 空 chunks / coverage 上限 / rerank_score=None
- 低置信度阈值 0.5：< 0.5 触发警告 / >= 0.5 不触发
- CHC-04 _parse_claims：直接数组 / 包装对象 / 围栏剥离 / 非法 status 过滤 / 空 claim 过滤
- check_faithfulness：happy path / 全 supported / 半 unverified / JSON 解析失败 skipped / 超时 skipped / LLM 异常 skipped
- append_unverified_warning：有 unverified 追加 / 无 unverified 不动 / 多 claim
- resolve_options 增量：enable_faithfulness_check 三层合并
- Schema：QueryOptions 接受字段 / QueryResponse 含 4 个新字段
- 端到端 v2_query：disabled 默认 / enabled 全 supported / enabled 含 unverified 触发 answer 追加 / 自检失败 skipped / 检索为空兜底

mock 策略：patch litellm.acompletion / hybrid_search / Tracer。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ════════════════════════════════════════════════════════════════
# 1. CHC-03 compute_confidence
# ════════════════════════════════════════════════════════════════


class TestComputeConfidence:
    def test_high_score_full_coverage(self):
        """rerank 均值 > 0.8 且全部被引用 → confidence > 0.8（PRD §559 验收）。"""
        from app.rag.confidence import compute_confidence

        chunks = [{"rerank_score": 0.92}, {"rerank_score": 0.85}, {"rerank_score": 0.88}]
        r = compute_confidence(cited_chunks=chunks, top_k=3)
        assert r.confidence > 0.8
        assert r.low_confidence_warning is None
        assert r.breakdown["coverage"] == 1.0

    def test_partial_coverage_lowers_confidence(self):
        """top_k=5 但只引用 2 条 → coverage=0.4 → confidence 显著降低。"""
        from app.rag.confidence import compute_confidence

        chunks = [{"rerank_score": 0.9}, {"rerank_score": 0.9}]
        r = compute_confidence(cited_chunks=chunks, top_k=5)
        assert r.breakdown["coverage"] == 0.4
        assert r.confidence == round(0.9 * 0.4, 4)

    def test_penalty_drops_confidence(self):
        """全 unverified（penalty=1）→ confidence=0.0 + warning。"""
        from app.rag.confidence import compute_confidence

        r = compute_confidence(
            cited_chunks=[{"rerank_score": 0.9}],
            top_k=1,
            hallucination_penalty=1.0,
        )
        assert r.confidence == 0.0
        assert r.low_confidence_warning is not None

    def test_empty_chunks_returns_zero_with_warning(self):
        """检索为空 → confidence=0 + 触发预警。"""
        from app.rag.confidence import compute_confidence

        r = compute_confidence(cited_chunks=[], top_k=5)
        assert r.confidence == 0.0
        assert r.low_confidence_warning is not None
        assert "0.00" in r.low_confidence_warning

    def test_low_confidence_triggers_warning(self):
        """confidence < 0.5 触发预警（PRD §553/556）。"""
        from app.rag.confidence import compute_confidence

        # rerank 均值 0.3 → confidence=0.3 < 0.5
        chunks = [{"rerank_score": 0.3}]
        r = compute_confidence(cited_chunks=chunks, top_k=1)
        assert r.confidence < 0.5
        assert r.low_confidence_warning is not None
        assert "建议人工核查" in r.low_confidence_warning

    def test_high_confidence_no_warning(self):
        from app.rag.confidence import compute_confidence

        chunks = [{"rerank_score": 0.9}]
        r = compute_confidence(cited_chunks=chunks, top_k=1)
        assert r.confidence >= 0.5
        assert r.low_confidence_warning is None

    def test_coverage_capped_at_one(self):
        """cited > top_k 极端边界 → coverage 不超 1.0。"""
        from app.rag.confidence import compute_confidence

        chunks = [{"rerank_score": 0.8}] * 10
        r = compute_confidence(cited_chunks=chunks, top_k=5)
        assert r.breakdown["coverage"] == 1.0
        assert r.confidence <= 1.0

    def test_rerank_score_none_safe(self):
        """rerank_score=None / 缺失 → 按 0 计，不抛错。"""
        from app.rag.confidence import compute_confidence

        chunks = [{"rerank_score": None}, {"rerank_score": 0.9}, {}]
        r = compute_confidence(cited_chunks=chunks, top_k=3)
        # 平均 (0 + 0.9 + 0) / 3 = 0.3
        assert r.breakdown["weighted_score"] == 0.3

    def test_warning_message_format(self):
        """警告文案严格按 PRD §556。"""
        from app.rag.confidence import compute_confidence

        r = compute_confidence(cited_chunks=[{"rerank_score": 0.3}], top_k=1)
        # PRD §556 关键字段
        assert "本次回答" in r.low_confidence_warning
        assert "置信度" in r.low_confidence_warning
        assert "建议人工核查" in r.low_confidence_warning


# ════════════════════════════════════════════════════════════════
# 2. CHC-04 _parse_claims
# ════════════════════════════════════════════════════════════════


class TestParseClaims:
    def test_direct_array(self):
        from app.rag.faithfulness import _parse_claims

        raw = json.dumps(
            [
                {"claim": "甲方付款", "status": "supported", "source_text": "甲方应于..."},
                {"claim": "金额是 100 万", "status": "unverified", "source_text": ""},
            ],
            ensure_ascii=False,
        )
        r = _parse_claims(raw)
        assert len(r) == 2
        assert r[0]["status"] == "supported"
        assert r[1]["status"] == "unverified"

    def test_wrapped_object(self):
        """部分模型返 {"claims": [...]} 包装。"""
        from app.rag.faithfulness import _parse_claims

        raw = json.dumps(
            {"claims": [{"claim": "x", "status": "supported", "source_text": "y"}]},
            ensure_ascii=False,
        )
        r = _parse_claims(raw)
        assert len(r) == 1

    def test_strip_code_fence(self):
        from app.rag.faithfulness import _parse_claims

        raw = '```json\n[{"claim": "x", "status": "supported", "source_text": "y"}]\n```'
        r = _parse_claims(raw)
        assert len(r) == 1

    def test_empty_array_returns_empty(self):
        from app.rag.faithfulness import _parse_claims

        r = _parse_claims("[]")
        assert r == []

    def test_invalid_status_filtered(self):
        from app.rag.faithfulness import _parse_claims

        raw = json.dumps(
            [
                {"claim": "ok", "status": "supported", "source_text": ""},
                {"claim": "bad", "status": "uncertain", "source_text": ""},  # 非法
            ],
            ensure_ascii=False,
        )
        r = _parse_claims(raw)
        assert len(r) == 1
        assert r[0]["claim"] == "ok"

    def test_empty_claim_filtered(self):
        from app.rag.faithfulness import _parse_claims

        raw = json.dumps(
            [
                {"claim": "", "status": "supported", "source_text": ""},
                {"claim": "  ", "status": "supported", "source_text": ""},
                {"claim": "real", "status": "supported", "source_text": ""},
            ],
            ensure_ascii=False,
        )
        r = _parse_claims(raw)
        assert len(r) == 1

    def test_invalid_json_returns_none(self):
        from app.rag.faithfulness import _parse_claims

        assert _parse_claims("not json") is None
        assert _parse_claims('{"unrelated": "obj"}') is None


# ════════════════════════════════════════════════════════════════
# 3. CHC-04 check_faithfulness
# ════════════════════════════════════════════════════════════════


def _llm_resp(content: str) -> MagicMock:
    resp = MagicMock()
    resp.model_dump = lambda: {"choices": [{"message": {"content": content}}]}
    return resp


class TestCheckFaithfulness:
    def test_resolve_kwargs_uses_unified_provider_prefix_resolution(self):
        """Faithfulness LLM kwargs 应复用统一前缀推断，Qwen/Qwen3 需补 openai/。"""
        from types import SimpleNamespace

        from app.rag.faithfulness import _resolve_kwargs

        with patch("app.rag.faithfulness.get_settings") as mock_settings:
            mock_settings.return_value = SimpleNamespace(
                faithfulness_model="Qwen/Qwen3-8B",
                litellm_model=None,
                litellm_api_base="https://api.siliconflow.cn/v1",
                litellm_api_key="sk-test",
                litellm_timeout=60.0,
                litellm_num_retries=0,
            )
            kwargs = _resolve_kwargs(messages=[{"role": "user", "content": "检查"}])
        assert kwargs["model"] == "openai/Qwen/Qwen3-8B"
        assert kwargs["api_base"] == "https://api.siliconflow.cn/v1"

    @pytest.mark.asyncio
    async def test_all_supported(self):
        from app.rag.faithfulness import check_faithfulness

        raw = json.dumps(
            [
                {"claim": "A", "status": "supported", "source_text": "支撑句 A"},
                {"claim": "B", "status": "supported", "source_text": "支撑句 B"},
            ],
            ensure_ascii=False,
        )
        with patch(
            "app.rag.faithfulness.litellm.acompletion",
            new=AsyncMock(return_value=_llm_resp(raw)),
        ):
            r = await check_faithfulness(answer="answer", context="context")
        assert r.status == "ok"
        assert len(r.claims) == 2
        assert r.unverified == []
        assert r.hallucination_penalty == 0.0

    @pytest.mark.asyncio
    async def test_half_unverified(self):
        from app.rag.faithfulness import check_faithfulness

        raw = json.dumps(
            [
                {"claim": "A", "status": "supported", "source_text": "源 A"},
                {"claim": "金额 100 万", "status": "unverified", "source_text": ""},
            ],
            ensure_ascii=False,
        )
        with patch(
            "app.rag.faithfulness.litellm.acompletion",
            new=AsyncMock(return_value=_llm_resp(raw)),
        ):
            r = await check_faithfulness(answer="答案", context="上下文")
        assert r.status == "ok"
        assert len(r.claims) == 2
        assert len(r.unverified) == 1
        assert r.hallucination_penalty == 0.5

    @pytest.mark.asyncio
    async def test_empty_claims(self):
        from app.rag.faithfulness import check_faithfulness

        with patch(
            "app.rag.faithfulness.litellm.acompletion",
            new=AsyncMock(return_value=_llm_resp("[]")),
        ):
            r = await check_faithfulness(answer="客气话", context="ctx")
        assert r.status == "ok"
        assert r.claims == []
        assert r.hallucination_penalty == 0.0

    @pytest.mark.asyncio
    async def test_json_parse_failure_skipped(self):
        from app.rag.faithfulness import check_faithfulness

        with patch(
            "app.rag.faithfulness.litellm.acompletion",
            new=AsyncMock(return_value=_llm_resp("不是 JSON")),
        ):
            r = await check_faithfulness(answer="a", context="c")
        assert r.status == "skipped"
        assert r.hallucination_penalty == 0.0

    @pytest.mark.asyncio
    async def test_llm_exception_skipped(self):
        from app.rag.faithfulness import check_faithfulness

        with patch(
            "app.rag.faithfulness.litellm.acompletion",
            new=AsyncMock(side_effect=RuntimeError("LLM down")),
        ):
            r = await check_faithfulness(answer="a", context="c")
        assert r.status == "skipped"

    @pytest.mark.asyncio
    async def test_timeout_skipped(self):
        from app.rag.faithfulness import check_faithfulness

        async def slow(*a, **kw):
            await asyncio.sleep(10)

        with patch(
            "app.rag.faithfulness.litellm.acompletion", new=AsyncMock(side_effect=slow)
        ), patch("app.rag.faithfulness.get_settings") as mock_get:
            mock_get.return_value = SimpleNamespace(
                faithfulness_model=None,
                litellm_model="deepseek/deepseek-chat",
                litellm_api_base=None,
                litellm_api_key=None,
                litellm_timeout=60.0,
                faithfulness_check_timeout_s=0.05,
            )
            r = await check_faithfulness(answer="a", context="c")
        assert r.status == "skipped"

    @pytest.mark.asyncio
    async def test_empty_answer_or_context_skipped(self):
        from app.rag.faithfulness import check_faithfulness

        r = await check_faithfulness(answer="", context="ctx")
        assert r.status == "skipped"
        r = await check_faithfulness(answer="ans", context="")
        assert r.status == "skipped"


# ════════════════════════════════════════════════════════════════
# 4. append_unverified_warning
# ════════════════════════════════════════════════════════════════


class TestAppendWarning:
    def test_append_when_unverified(self):
        from app.rag.faithfulness import append_unverified_warning

        ans = "原始答案"
        unverified = [
            {"claim": "金额是 100 万", "status": "unverified"},
            {"claim": "签订日期", "status": "unverified"},
        ]
        out = append_unverified_warning(ans, unverified)
        assert "原始答案" in out
        assert "⚠" in out
        assert "金额是 100 万" in out
        assert "签订日期" in out

    def test_no_unverified_unchanged(self):
        from app.rag.faithfulness import append_unverified_warning

        ans = "原始答案"
        out = append_unverified_warning(ans, [])
        assert out == ans


# ════════════════════════════════════════════════════════════════
# 5. resolve_options 增量
# ════════════════════════════════════════════════════════════════


class TestResolveFaithfulness:
    def _settings_stub(self, **overrides):
        from app.core.config import get_settings

        s = get_settings()
        for k, v in overrides.items():
            object.__setattr__(s, k, v)
        return s

    def test_default_false_from_settings(self):
        from app.rag.retrieval_config import resolve_options
        from app.schemas.v2.query import QueryOptions

        s = self._settings_stub(faithfulness_check_default=False)
        r = resolve_options(options=QueryOptions(), kb=None, settings=s)
        assert r.enable_faithfulness_check is False

    def test_settings_default_true(self):
        from app.rag.retrieval_config import resolve_options
        from app.schemas.v2.query import QueryOptions

        s = self._settings_stub(faithfulness_check_default=True)
        r = resolve_options(options=QueryOptions(), kb=None, settings=s)
        assert r.enable_faithfulness_check is True

    def test_kb_overrides_settings(self):
        from app.rag.retrieval_config import resolve_options
        from app.schemas.v2.query import QueryOptions

        s = self._settings_stub(faithfulness_check_default=False)
        kb = SimpleNamespace(
            retrieval_config={"enable_faithfulness_check": True}
        )
        r = resolve_options(options=QueryOptions(), kb=kb, settings=s)
        assert r.enable_faithfulness_check is True

    def test_api_overrides_kb_with_explicit_false(self):
        """API 显式 False 覆盖 KB True 覆盖 settings True。"""
        from app.rag.retrieval_config import resolve_options
        from app.schemas.v2.query import QueryOptions

        s = self._settings_stub(faithfulness_check_default=True)
        kb = SimpleNamespace(
            retrieval_config={"enable_faithfulness_check": True}
        )
        opts = QueryOptions(enable_faithfulness_check=False)
        r = resolve_options(options=opts, kb=kb, settings=s)
        assert r.enable_faithfulness_check is False


# ════════════════════════════════════════════════════════════════
# 6. Schema 字段
# ════════════════════════════════════════════════════════════════


class TestSchemas:
    def test_query_options_accepts_faithfulness(self):
        from app.schemas.v2.query import QueryOptions

        opts = QueryOptions(enable_faithfulness_check=True)
        assert opts.enable_faithfulness_check is True
        # 默认 None（未指定）
        opts2 = QueryOptions()
        assert opts2.enable_faithfulness_check is None

    def test_query_response_new_fields(self):
        from app.schemas.v2.query import QueryResponse

        resp = QueryResponse(
            answer="答案",
            confidence=0.85,
            low_confidence_warning=None,
            faithfulness_check="ok",
            unverified_claims=[{"claim": "x", "status": "unverified"}],
        )
        assert resp.confidence == 0.85
        assert resp.faithfulness_check == "ok"
        assert resp.unverified_claims[0]["claim"] == "x"

    def test_confidence_range_validation(self):
        from pydantic import ValidationError

        from app.schemas.v2.query import QueryResponse

        # confidence 超出 [0, 1] 范围
        with pytest.raises(ValidationError):
            QueryResponse(answer="a", confidence=1.5)
        with pytest.raises(ValidationError):
            QueryResponse(answer="a", confidence=-0.1)


# ════════════════════════════════════════════════════════════════
# 7. 端到端 v2_query
# ════════════════════════════════════════════════════════════════


def _make_tracer_mock():
    tracer = MagicMock()
    tracer.trace_id = "trace-t9"
    tracer.__aenter__ = AsyncMock(return_value=tracer)
    tracer.__aexit__ = AsyncMock(return_value=False)
    step = MagicMock()
    step.__enter__ = MagicMock(return_value=step)
    step.__exit__ = MagicMock(return_value=False)
    tracer.step = MagicMock(return_value=step)
    return tracer


def _make_rewrite_noop():
    """构造一个 RewriteResult(none) 给 mock。"""
    from app.rag.query_rewriter import RewriteResult

    return RewriteResult()


class TestV2QueryE2E:
    @pytest.mark.asyncio
    async def test_disabled_skips_faithfulness_call(self):
        """默认 disabled 时不调 check_faithfulness；响应字段 faithfulness_check='disabled'。"""
        from app.api.v2.endpoints.query import v2_query
        from app.rag.hybrid_retriever import HybridSearchResult
        from app.schemas.v2.query import QueryRequest

        results = [
            HybridSearchResult(
                chunk_id=1, content="x", document_id="d", score=0.9,
                metadata={"filename": "f.pdf"},
            ),
        ]
        llm_resp = MagicMock()
        llm_resp.choices = [MagicMock(message=MagicMock(content="ans [1]"))]

        check_mock = AsyncMock()

        with patch(
            "app.api.v2.endpoints.query.hybrid_search",
            new=AsyncMock(return_value=results),
        ), patch(
            "app.api.v2.endpoints.query.Tracer", return_value=_make_tracer_mock()
        ), patch(
            "app.api.v2.endpoints.query.rewrite_query", new=AsyncMock(return_value=_make_rewrite_noop())
        ), patch(
            "app.api.v2.endpoints.query.extract_query_entities", new=AsyncMock(return_value=[])
        ), patch(
            "app.api.v2.endpoints.query.anchor_to_graph", new=AsyncMock(return_value=[])
        ), patch(
            "app.api.v2.endpoints.query.check_faithfulness", new=check_mock
        ), patch(
            "litellm.acompletion", new=AsyncMock(return_value=llm_resp)
        ):
            mock_db = AsyncMock()
            mock_db.get = AsyncMock(return_value=None)
            req = QueryRequest(query="什么是 X？")
            resp = await v2_query(body=req, db=mock_db)

        # disabled 时不调自检
        check_mock.assert_not_called()
        assert resp.faithfulness_check == "disabled"
        assert resp.unverified_claims is None
        # confidence 仍计算（无 penalty）
        assert resp.confidence is not None and resp.confidence > 0

    @pytest.mark.asyncio
    async def test_enabled_all_supported(self):
        """开启自检 + 全 supported → confidence 不被惩罚 + 答案不追加警告。"""
        from app.api.v2.endpoints.query import v2_query
        from app.rag.faithfulness import FaithfulnessResult
        from app.rag.hybrid_retriever import HybridSearchResult
        from app.schemas.v2.query import QueryOptions, QueryRequest

        results = [
            HybridSearchResult(
                chunk_id=1, content="x", document_id="d", score=0.9,
                metadata={"filename": "f.pdf"},
            ),
        ]
        llm_resp = MagicMock()
        llm_resp.choices = [MagicMock(message=MagicMock(content="原始答案 [1]"))]

        faith_ok = FaithfulnessResult(
            status="ok",
            claims=[{"claim": "X", "status": "supported", "source_text": "x"}],
            unverified=[],
            hallucination_penalty=0.0,
        )

        with patch(
            "app.api.v2.endpoints.query.hybrid_search",
            new=AsyncMock(return_value=results),
        ), patch(
            "app.api.v2.endpoints.query.Tracer", return_value=_make_tracer_mock()
        ), patch(
            "app.api.v2.endpoints.query.rewrite_query", new=AsyncMock(return_value=_make_rewrite_noop())
        ), patch(
            "app.api.v2.endpoints.query.extract_query_entities", new=AsyncMock(return_value=[])
        ), patch(
            "app.api.v2.endpoints.query.anchor_to_graph", new=AsyncMock(return_value=[])
        ), patch(
            "app.api.v2.endpoints.query.check_faithfulness", new=AsyncMock(return_value=faith_ok)
        ), patch(
            "litellm.acompletion", new=AsyncMock(return_value=llm_resp)
        ):
            mock_db = AsyncMock()
            mock_db.get = AsyncMock(return_value=None)
            req = QueryRequest(
                query="x",
                options=QueryOptions(enable_faithfulness_check=True),
            )
            resp = await v2_query(body=req, db=mock_db)

        assert resp.faithfulness_check == "ok"
        assert resp.unverified_claims is None  # 空 unverified → None
        # answer 未被追加警告
        assert "⚠" not in resp.answer

    @pytest.mark.asyncio
    async def test_enabled_with_unverified_appends_warning(self):
        """开启自检 + unverified → answer 末尾追加警告 + 响应含 unverified_claims。"""
        from app.api.v2.endpoints.query import v2_query
        from app.rag.faithfulness import FaithfulnessResult
        from app.rag.hybrid_retriever import HybridSearchResult
        from app.schemas.v2.query import QueryOptions, QueryRequest

        results = [
            HybridSearchResult(
                chunk_id=1, content="x", document_id="d", score=0.9,
                metadata={"filename": "f.pdf"},
            ),
        ]
        llm_resp = MagicMock()
        llm_resp.choices = [MagicMock(message=MagicMock(content="合同金额 100 万元 [1]"))]

        unv = [{"claim": "合同金额 100 万元", "status": "unverified", "source_text": ""}]
        faith = FaithfulnessResult(
            status="ok", claims=unv, unverified=unv, hallucination_penalty=1.0,
        )

        with patch(
            "app.api.v2.endpoints.query.hybrid_search",
            new=AsyncMock(return_value=results),
        ), patch(
            "app.api.v2.endpoints.query.Tracer", return_value=_make_tracer_mock()
        ), patch(
            "app.api.v2.endpoints.query.rewrite_query", new=AsyncMock(return_value=_make_rewrite_noop())
        ), patch(
            "app.api.v2.endpoints.query.extract_query_entities", new=AsyncMock(return_value=[])
        ), patch(
            "app.api.v2.endpoints.query.anchor_to_graph", new=AsyncMock(return_value=[])
        ), patch(
            "app.api.v2.endpoints.query.check_faithfulness", new=AsyncMock(return_value=faith)
        ), patch(
            "litellm.acompletion", new=AsyncMock(return_value=llm_resp)
        ):
            mock_db = AsyncMock()
            mock_db.get = AsyncMock(return_value=None)
            req = QueryRequest(
                query="合同金额是多少？",
                options=QueryOptions(enable_faithfulness_check=True),
            )
            resp = await v2_query(body=req, db=mock_db)

        assert resp.faithfulness_check == "ok"
        assert resp.unverified_claims and len(resp.unverified_claims) == 1
        # answer 追加了警告清单
        assert "⚠" in resp.answer
        assert "合同金额 100 万元" in resp.answer
        # penalty=1.0 → confidence 应被惩罚为 0
        assert resp.confidence == 0.0
        assert resp.low_confidence_warning is not None

    @pytest.mark.asyncio
    async def test_faithfulness_skipped_keeps_response_intact(self):
        """自检失败（skipped）时 confidence 不被惩罚，主链路正常。"""
        from app.api.v2.endpoints.query import v2_query
        from app.rag.faithfulness import FaithfulnessResult
        from app.rag.hybrid_retriever import HybridSearchResult
        from app.schemas.v2.query import QueryOptions, QueryRequest

        results = [
            HybridSearchResult(
                chunk_id=1, content="x", document_id="d", score=0.9,
                metadata={"filename": "f.pdf"},
            ),
        ]
        llm_resp = MagicMock()
        llm_resp.choices = [MagicMock(message=MagicMock(content="ans [1]"))]

        faith_skipped = FaithfulnessResult(status="skipped")

        with patch(
            "app.api.v2.endpoints.query.hybrid_search",
            new=AsyncMock(return_value=results),
        ), patch(
            "app.api.v2.endpoints.query.Tracer", return_value=_make_tracer_mock()
        ), patch(
            "app.api.v2.endpoints.query.rewrite_query", new=AsyncMock(return_value=_make_rewrite_noop())
        ), patch(
            "app.api.v2.endpoints.query.extract_query_entities", new=AsyncMock(return_value=[])
        ), patch(
            "app.api.v2.endpoints.query.anchor_to_graph", new=AsyncMock(return_value=[])
        ), patch(
            "app.api.v2.endpoints.query.check_faithfulness",
            new=AsyncMock(return_value=faith_skipped),
        ), patch(
            "litellm.acompletion", new=AsyncMock(return_value=llm_resp)
        ):
            mock_db = AsyncMock()
            mock_db.get = AsyncMock(return_value=None)
            req = QueryRequest(
                query="x",
                options=QueryOptions(enable_faithfulness_check=True),
            )
            resp = await v2_query(body=req, db=mock_db)

        assert resp.faithfulness_check == "skipped"
        # 没 unverified → None
        assert resp.unverified_claims is None
        # confidence 计算时 penalty=0（skipped 默认 0），主链路正常
        assert resp.confidence is not None and resp.confidence > 0

    @pytest.mark.asyncio
    async def test_empty_retrieval_returns_zero_confidence(self):
        """检索空兜底分支也透 confidence=0 + warning + faithfulness 字段。"""
        from app.api.v2.endpoints.query import v2_query
        from app.schemas.v2.query import QueryOptions, QueryRequest

        with patch(
            "app.api.v2.endpoints.query.hybrid_search",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.api.v2.endpoints.query.Tracer", return_value=_make_tracer_mock()
        ), patch(
            "app.api.v2.endpoints.query.rewrite_query", new=AsyncMock(return_value=_make_rewrite_noop())
        ), patch(
            "app.api.v2.endpoints.query.extract_query_entities", new=AsyncMock(return_value=[])
        ), patch(
            "app.api.v2.endpoints.query.anchor_to_graph", new=AsyncMock(return_value=[])
        ), patch(
            "litellm.acompletion", new=AsyncMock()
        ) as mock_acomp:
            mock_db = AsyncMock()
            mock_db.get = AsyncMock(return_value=None)
            req = QueryRequest(
                query="不存在",
                options=QueryOptions(enable_faithfulness_check=True),
            )
            resp = await v2_query(body=req, db=mock_db)

        # 检索空 → 兜底文案 + 不调 LLM
        mock_acomp.assert_not_called()
        assert "未检索到" in resp.answer
        assert resp.confidence == 0.0
        assert resp.low_confidence_warning is not None
        # enable_faithfulness_check=True + 检索空 → 标 skipped
        assert resp.faithfulness_check == "skipped"
        assert resp.unverified_claims is None
