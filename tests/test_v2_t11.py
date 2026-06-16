"""V2.0 T11 · RAGAS 评估 单测（EVA-01/02/03）。

覆盖矩阵：
- Schemas：EvalCreateRequest / EvalSummary / EvalDetailResponse 校验
- ragas_evaluator：_to_float_or_none / _compute_overall / METRIC_FIELDS；
  evaluate_with_ragas 在 ragas 缺失 / 空 samples / mock evaluate / 单题指标解析
- eval_runner：mock hybrid_search + generate_answer 验 contexts/answer 提取；
  检索为空兜底；hybrid_search 异常软失败
- eval_task（Celery 主流程）：状态机 pending→processing→completed；
  EvalTask 不存在 → skipped；单题超时不阻断；LLM 配置缺失 → failed
- API endpoints：POST 空 eval_set → 40012；超 100 → 40013；正常 → eval_task_id；
  GET 不存在 → 40400；GET 列表 kb_id 隔离；router 注册路径
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ════════════════════════════════════════════════════════════════
# 1. Schemas（EvalCreateRequest / EvalSummary / EvalDetailItem）
# ════════════════════════════════════════════════════════════════


class TestEvalSchemas:
    def test_eval_qa_item_min_length(self):
        """question / ground_truth 不能为空字符串。"""
        from pydantic import ValidationError
        from app.schemas.v2.eval import EvalQAItem

        with pytest.raises(ValidationError):
            EvalQAItem(question="", ground_truth="abc")
        with pytest.raises(ValidationError):
            EvalQAItem(question="abc", ground_truth="")

    def test_eval_create_request_accepts_min_fields(self):
        """最小入参：仅 eval_set 必填，retrieval_options 默认空对象。"""
        from app.schemas.v2.eval import EvalCreateRequest

        req = EvalCreateRequest(eval_set=[{"question": "q1", "ground_truth": "a1"}])
        assert len(req.eval_set) == 1
        assert req.retrieval_options.top_k is None
        assert req.name is None

    def test_eval_summary_clamps_range(self):
        """EvalSummary 每项范围 [0, 1]，越界报错。"""
        from pydantic import ValidationError
        from app.schemas.v2.eval import EvalSummary

        s = EvalSummary(faithfulness=0.5, answer_relevancy=0.8,
                        context_precision=None, context_recall=1.0,
                        overall_score=0.77)
        assert s.faithfulness == 0.5
        assert s.context_precision is None

        with pytest.raises(ValidationError):
            EvalSummary(faithfulness=1.5)

    def test_eval_detail_response_minimal(self):
        """EvalDetailResponse 最少必填字段：eval_task_id/kb_id/status/created_at。"""
        from app.schemas.v2.eval import EvalDetailResponse

        now = datetime.now(timezone.utc)
        r = EvalDetailResponse(
            eval_task_id=uuid.uuid4(),
            kb_id=uuid.uuid4(),
            status="pending",
            created_at=now,
        )
        assert r.progress == 0
        assert r.summary is None


# ════════════════════════════════════════════════════════════════
# 2. ragas_evaluator 辅助函数
# ════════════════════════════════════════════════════════════════


class TestRagasHelpers:
    def test_to_float_or_none_handles_nan(self):
        from app.rag.ragas_evaluator import _to_float_or_none

        assert _to_float_or_none(0.7) == 0.7
        assert _to_float_or_none("0.5") == 0.5
        assert _to_float_or_none(None) is None
        assert _to_float_or_none("abc") is None
        assert _to_float_or_none(float("nan")) is None
        assert _to_float_or_none(float("inf")) is None

    def test_compute_overall_arithmetic_mean(self):
        from app.rag.ragas_evaluator import _compute_overall

        # 4 项均有值
        v = _compute_overall({"a": 0.8, "b": 0.6, "c": 0.4, "d": 1.0})
        assert v == pytest.approx(0.7)
        # 含 None：忽略后算均值
        v = _compute_overall({"a": 0.8, "b": None, "c": 0.4, "d": None})
        assert v == pytest.approx(0.6)
        # 全 None
        assert _compute_overall({"a": None, "b": None}) is None

    def test_metric_fields_constant(self):
        from app.rag.ragas_evaluator import METRIC_FIELDS

        assert METRIC_FIELDS == (
            "faithfulness", "answer_relevancy", "context_precision", "context_recall",
        )


# ════════════════════════════════════════════════════════════════
# 3. ragas_evaluator.evaluate_with_ragas
# ════════════════════════════════════════════════════════════════


class TestEvaluateWithRagas:
    @pytest.mark.asyncio
    async def test_empty_samples_short_circuits(self):
        """samples 为空 → 立即返回空 summary，不 import ragas。"""
        from app.rag.ragas_evaluator import evaluate_with_ragas

        r = await evaluate_with_ragas(
            samples=[], llm_model="x", embedding_model="y",
        )
        assert r["summary"]["overall_score"] is None
        assert r["details"] == []
        assert r["error"] == "samples is empty"

    @pytest.mark.asyncio
    async def test_ragas_import_failure_returns_none_summary(self):
        """ragas 缺失 / import 失败 → summary 全 None，details 保留原 samples 字段。"""
        from app.rag.ragas_evaluator import evaluate_with_ragas

        # 用 patch builtins.__import__ 模拟 ImportError
        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
            else __import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("ragas"):
                raise ImportError("simulated missing ragas")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            r = await evaluate_with_ragas(
                samples=[{"question": "q1", "ground_truth": "a1",
                          "answer": "ans1", "contexts": ["c1"]}],
                llm_model="deepseek/deepseek-v4-flash",
                embedding_model="openai/Qwen/Qwen3-Embedding-8B",
            )
        assert r["summary"]["overall_score"] is None
        assert "ragas import failed" in (r["error"] or "")
        assert len(r["details"]) == 1
        assert r["details"][0]["question"] == "q1"

    @pytest.mark.asyncio
    async def test_embedding_missing_returns_init_failure(self):
        """embedding_model 未配置时，wrapper 初始化失败，summary 全 None。"""
        from app.rag.ragas_evaluator import evaluate_with_ragas

        # mock _build_evaluator_llm 成功（ragas import 成功的前提）
        # 但 _build_evaluator_embeddings 因 embedding_model=None 抛 ValueError
        with patch("app.rag.ragas_evaluator._build_evaluator_llm",
                   return_value=MagicMock()):
            r = await evaluate_with_ragas(
                samples=[{"question": "q1", "ground_truth": "a1",
                          "answer": "ans", "contexts": ["c"]}],
                llm_model="deepseek/x",
                embedding_model=None,
            )
        # ragas import 成功 → 落到 evaluator init 失败分支
        # （若环境无 ragas，会先报 ragas import failed，也算合法）
        assert r["summary"]["overall_score"] is None
        assert r["error"] is not None

    @pytest.mark.asyncio
    async def test_evaluate_parses_scores_correctly(self):
        """mock ragas.evaluate 返回 fake EvaluationResult → 验证字段解析。

        本测试用 sys.modules 注入 fake ragas，绕过环境里 ragas 的 import 错误
        （0.2.x 老版本会 import langchain_community.chat_models.vertexai 失败）。
        """
        import sys
        from app.rag.ragas_evaluator import evaluate_with_ragas

        # 构造 fake ragas 包结构
        fake_result = SimpleNamespace(scores=[
            {"faithfulness": 0.9, "answer_relevancy": 0.8,
             "context_precision": 0.7, "context_recall": 1.0},
            {"faithfulness": 0.5, "answer_relevancy": 0.4,
             "context_precision": 0.6, "context_recall": float("nan")},
        ])

        fake_ragas = SimpleNamespace(evaluate=MagicMock(return_value=fake_result))
        fake_dataset_schema = SimpleNamespace(
            EvaluationDataset=MagicMock(),
            SingleTurnSample=MagicMock(),
        )
        fake_metrics = SimpleNamespace(
            Faithfulness=MagicMock(),
            AnswerRelevancy=MagicMock(),
            ContextPrecision=MagicMock(),
            ContextRecall=MagicMock(),
        )

        # 备份原模块以便恢复
        saved = {k: sys.modules.get(k) for k in
                 ("ragas", "ragas.dataset_schema", "ragas.metrics")}
        sys.modules["ragas"] = fake_ragas
        sys.modules["ragas.dataset_schema"] = fake_dataset_schema
        sys.modules["ragas.metrics"] = fake_metrics

        try:
            with patch("app.rag.ragas_evaluator._build_evaluator_llm",
                       return_value=MagicMock()), \
                 patch("app.rag.ragas_evaluator._build_evaluator_embeddings",
                       return_value=MagicMock()):

                samples = [
                    {"question": "q1", "ground_truth": "a1", "answer": "x",
                     "contexts": ["c"]},
                    {"question": "q2", "ground_truth": "a2", "answer": "y",
                     "contexts": ["c"]},
                ]
                r = await evaluate_with_ragas(
                    samples=samples,
                    llm_model="deepseek/x",
                    embedding_model="openai/y",
                )
        finally:
            # 恢复原模块（None 表示原本不在）
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

        # summary：第 2 题 context_recall=NaN → 不计入均值
        assert r["summary"]["faithfulness"] == pytest.approx((0.9 + 0.5) / 2)
        assert r["summary"]["context_recall"] == 1.0  # 仅第一题有效
        assert r["summary"]["overall_score"] is not None
        assert r["error"] is None
        assert len(r["details"]) == 2
        assert r["details"][1]["context_recall"] is None  # NaN 转 None
        assert r["details"][0]["faithfulness"] == 0.9


# ════════════════════════════════════════════════════════════════
# 4. eval_runner.run_single_query_for_eval
# ════════════════════════════════════════════════════════════════


class TestEvalRunner:
    @pytest.mark.asyncio
    async def test_returns_answer_and_contexts(self):
        """正常路径：返回 {answer, contexts, source_citations}。"""
        from app.rag.eval_runner import run_single_query_for_eval
        from app.rag.hybrid_retriever import HybridSearchResult

        mock_results = [
            HybridSearchResult(
                chunk_id=1, content="台风原文", document_id="d1",
                score=0.9, heading_path=["§1"], block_type="paragraph",
                page_number=3, metadata={"filename": "x.pdf"},
            ),
            HybridSearchResult(
                chunk_id=2, content="另一段", document_id="d2",
                score=0.7, heading_path=[], block_type="paragraph",
            ),
        ]

        with patch("app.rag.eval_runner.hybrid_search",
                   new=AsyncMock(return_value=mock_results)), \
             patch("app.rag.eval_runner.generate_answer",
                   new=AsyncMock(return_value="生成的答案 [1][2]")), \
             patch("app.rag.eval_runner.rewrite_query",
                   new=AsyncMock(side_effect=lambda q, s: __import__(
                       "app.rag.query_rewriter", fromlist=["RewriteResult"]
                   ).RewriteResult())), \
             patch("app.rag.eval_runner.extract_query_entities",
                   new=AsyncMock(return_value=[])), \
             patch("app.rag.eval_runner.anchor_to_graph",
                   new=AsyncMock(return_value=[])):
            mock_db = AsyncMock()
            mock_db.get = AsyncMock(return_value=None)
            r = await run_single_query_for_eval(
                query="什么是台风？",
                kb_ids=[uuid.uuid4()],
                options={"top_k": 5},
                db=mock_db,
            )

        assert r["answer"] == "生成的答案 [1][2]"
        assert r["contexts"] == ["台风原文", "另一段"]
        assert len(r["source_citations"]) == 2
        assert r["source_citations"][0]["document_name"] == "x.pdf"
        assert r["error"] is None

    @pytest.mark.asyncio
    async def test_empty_retrieval_returns_marker(self):
        """检索为空 → answer 标"未检索到相关内容"，不调 LLM。"""
        from app.rag.eval_runner import run_single_query_for_eval

        with patch("app.rag.eval_runner.hybrid_search",
                   new=AsyncMock(return_value=[])), \
             patch("app.rag.eval_runner.rewrite_query",
                   new=AsyncMock(side_effect=lambda q, s: __import__(
                       "app.rag.query_rewriter", fromlist=["RewriteResult"]
                   ).RewriteResult())), \
             patch("app.rag.eval_runner.extract_query_entities",
                   new=AsyncMock(return_value=[])), \
             patch("app.rag.eval_runner.anchor_to_graph",
                   new=AsyncMock(return_value=[])):
            mock_db = AsyncMock()
            mock_db.get = AsyncMock(return_value=None)
            r = await run_single_query_for_eval(
                query="q",
                kb_ids=[],
                options=None,
                db=mock_db,
            )

        assert r["contexts"] == []
        assert "未检索到" in r["answer"]
        assert r["error"] is None

    @pytest.mark.asyncio
    async def test_hybrid_search_exception_soft_fails(self):
        """hybrid_search 抛错 → 返回 error 字段，answer 标"检索失败"。"""
        from app.rag.eval_runner import run_single_query_for_eval

        with patch("app.rag.eval_runner.hybrid_search",
                   new=AsyncMock(side_effect=RuntimeError("milvus down"))), \
             patch("app.rag.eval_runner.rewrite_query",
                   new=AsyncMock(side_effect=lambda q, s: __import__(
                       "app.rag.query_rewriter", fromlist=["RewriteResult"]
                   ).RewriteResult())), \
             patch("app.rag.eval_runner.extract_query_entities",
                   new=AsyncMock(return_value=[])), \
             patch("app.rag.eval_runner.anchor_to_graph",
                   new=AsyncMock(return_value=[])):
            mock_db = AsyncMock()
            mock_db.get = AsyncMock(return_value=None)
            r = await run_single_query_for_eval(
                query="q", kb_ids=[], options=None, db=mock_db,
            )

        assert r["contexts"] == []
        assert "检索失败" in r["answer"]
        assert "milvus down" in (r["error"] or "")

    @pytest.mark.asyncio
    async def test_generate_answer_exception_keeps_contexts(self):
        """LLM 生成失败但检索成功 → contexts/source_citations 保留，answer 标错。"""
        from app.rag.eval_runner import run_single_query_for_eval
        from app.rag.hybrid_retriever import HybridSearchResult

        with patch("app.rag.eval_runner.hybrid_search",
                   new=AsyncMock(return_value=[HybridSearchResult(
                       chunk_id=1, content="ctx1", document_id="d", score=0.8,
                   )])), \
             patch("app.rag.eval_runner.generate_answer",
                   new=AsyncMock(side_effect=RuntimeError("llm 500"))), \
             patch("app.rag.eval_runner.rewrite_query",
                   new=AsyncMock(side_effect=lambda q, s: __import__(
                       "app.rag.query_rewriter", fromlist=["RewriteResult"]
                   ).RewriteResult())), \
             patch("app.rag.eval_runner.extract_query_entities",
                   new=AsyncMock(return_value=[])), \
             patch("app.rag.eval_runner.anchor_to_graph",
                   new=AsyncMock(return_value=[])):
            mock_db = AsyncMock()
            mock_db.get = AsyncMock(return_value=None)
            r = await run_single_query_for_eval(
                query="q", kb_ids=[], options=None, db=mock_db,
            )

        assert r["contexts"] == ["ctx1"]
        assert "LLM 调用错误" in r["answer"]
        assert "llm 500" in (r["error"] or "")


# ════════════════════════════════════════════════════════════════
# 5. eval_task._resolve_eval_llm_kwargs
# ════════════════════════════════════════════════════════════════


class TestResolveEvalLLMKwargs:
    def test_uses_override_when_provided(self):
        from app.tasks.eval_task import _resolve_eval_llm_kwargs

        with patch("app.tasks.eval_task.get_settings") as mock_get:
            mock_get.return_value = SimpleNamespace(
                litellm_model="deepseek/x",
                litellm_api_key="key1",
                litellm_api_base="https://api.deepseek.com",
            )
            r = _resolve_eval_llm_kwargs("deepseek/v4-flash")
        assert r["model"] == "deepseek/v4-flash"
        assert r["api_key"] == "key1"

    def test_falls_back_to_litellm_model(self):
        from app.tasks.eval_task import _resolve_eval_llm_kwargs

        with patch("app.tasks.eval_task.get_settings") as mock_get:
            mock_get.return_value = SimpleNamespace(
                litellm_model="deepseek/chat",
                litellm_api_key=None,
                litellm_api_base=None,
            )
            r = _resolve_eval_llm_kwargs(None)
        assert r["model"] == "deepseek/chat"

    def test_raises_when_both_missing(self):
        from app.tasks.eval_task import _resolve_eval_llm_kwargs

        with patch("app.tasks.eval_task.get_settings") as mock_get:
            mock_get.return_value = SimpleNamespace(
                litellm_model=None,
                litellm_api_key=None,
                litellm_api_base=None,
            )
            with pytest.raises(ValueError, match="EVAL_LLM_MODEL"):
                _resolve_eval_llm_kwargs(None)

    def test_auto_prefix_for_deepseek_base(self):
        """裸模型名（不带厂商前缀）+ deepseek api_base → 自动补 deepseek/ 前缀。"""
        from app.tasks.eval_task import _resolve_eval_llm_kwargs

        with patch("app.tasks.eval_task.get_settings") as mock_get:
            mock_get.return_value = SimpleNamespace(
                litellm_model=None,
                litellm_api_key="k",
                litellm_api_base="https://api.deepseek.com/v1",
            )
            r = _resolve_eval_llm_kwargs("v4-pro")
        assert r["model"] == "deepseek/v4-pro"


# ════════════════════════════════════════════════════════════════
# 6. eval_task._run_evaluation_main（Celery 主流程）
# ════════════════════════════════════════════════════════════════


class _FakeResources:
    """task_resources() 的最小 mock：仅暴露 db() 上下文。"""

    def __init__(self, session):
        self._session = session

    def db(self):
        # 返回支持 async with 的 session（按调用次数累计同一个 session）
        outer_self = self

        class _Ctx:
            async def __aenter__(self):
                return outer_self._session

            async def __aexit__(self, *args):
                return False

        return _Ctx()


def _make_eval_task_row(eval_task_id, *, qa_set, kb_id):
    from app.models.eval_task import EVAL_STATUS_PENDING

    row = MagicMock()
    row.id = eval_task_id
    row.kb_id = kb_id
    row.eval_dataset = {"eval_set": qa_set}
    row.eval_config = {"retrieval_options": {"top_k": 3}}
    row.question_count = len(qa_set)
    row.status = EVAL_STATUS_PENDING
    return row


class TestEvalTaskMainFlow:
    @pytest.mark.asyncio
    async def test_not_found_returns_skipped(self):
        """EvalTask 不存在 → 返 skipped。"""
        from app.tasks.eval_task import _run_evaluation_main

        # mock session.execute().scalar_one_or_none() → None
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        ))
        mock_session.commit = AsyncMock()

        fake_res = _FakeResources(mock_session)

        @asyncio.coroutine if False else (lambda f: f)
        def _patch_task_res():
            pass

        # 用 patch 替换 task_resources 上下文
        class _CM:
            async def __aenter__(self):
                return fake_res

            async def __aexit__(self, *args):
                return False

        with patch("app.rag.milvus_client.init_milvus", return_value=MagicMock()), \
             patch("app.tasks.eval_task.task_resources", return_value=_CM()):
            r = await _run_evaluation_main(str(uuid.uuid4()))

        assert r["status"] == "skipped"
        assert r["reason"] == "eval_task_not_found"

    @pytest.mark.asyncio
    async def test_full_flow_marks_completed(self):
        """正常流程：processing → 跑 RAG → ragas 打分 → completed。"""
        from app.tasks.eval_task import _run_evaluation_main

        eval_id = uuid.uuid4()
        kb_id = uuid.uuid4()
        qa_set = [{"question": "q1", "ground_truth": "a1"}]
        row = _make_eval_task_row(eval_id, qa_set=qa_set, kb_id=kb_id)

        # mock session 行为
        mock_session = AsyncMock()
        scalar_mock = MagicMock(scalar_one_or_none=MagicMock(return_value=row))
        mock_session.execute = AsyncMock(return_value=scalar_mock)
        mock_session.commit = AsyncMock()

        fake_res = _FakeResources(mock_session)

        class _CM:
            async def __aenter__(self):
                return fake_res

            async def __aexit__(self, *args):
                return False

        # mock 单题 RAG + ragas
        fake_rag_result = {
            "answer": "answer1",
            "contexts": ["ctx1"],
            "source_citations": [],
            "error": None,
        }
        fake_ragas = {
            "summary": {"faithfulness": 0.9, "answer_relevancy": 0.8,
                        "context_precision": 0.7, "context_recall": 1.0,
                        "overall_score": 0.85},
            "details": [{"question": "q1", "ground_truth": "a1",
                         "answer": "answer1", "contexts": ["ctx1"],
                         "faithfulness": 0.9, "answer_relevancy": 0.8,
                         "context_precision": 0.7, "context_recall": 1.0,
                         "error": None}],
            "metric_backend": "ragas-0.2",
            "error": None,
        }

        with patch("app.rag.milvus_client.init_milvus", return_value=MagicMock()), \
             patch("app.tasks.eval_task.task_resources", return_value=_CM()), \
             patch("app.tasks.eval_task.get_settings") as mock_get_s, \
             patch("app.rag.eval_runner.run_single_query_for_eval",
                   new=AsyncMock(return_value=fake_rag_result)), \
             patch("app.rag.ragas_evaluator.evaluate_with_ragas",
                   new=AsyncMock(return_value=fake_ragas)):
            mock_get_s.return_value = SimpleNamespace(
                eval_question_timeout_s=10.0,
                eval_llm_model=None,
                litellm_model="deepseek/x",
                litellm_api_key="k",
                litellm_api_base="https://api.deepseek.com",
                embedding_model="openai/y",
                embedding_api_key="ek",
                embedding_api_base="https://siliconflow.cn",
            )
            r = await _run_evaluation_main(str(eval_id))

        assert r["status"] == "completed"
        assert r["summary"]["overall_score"] == 0.85

    @pytest.mark.asyncio
    async def test_single_question_timeout_soft_fail(self):
        """单题超时 → answer 标超时，继续打分；整批 completed。"""
        from app.tasks.eval_task import _run_evaluation_main

        eval_id = uuid.uuid4()
        kb_id = uuid.uuid4()
        qa_set = [{"question": "q1", "ground_truth": "a1"}]
        row = _make_eval_task_row(eval_id, qa_set=qa_set, kb_id=kb_id)

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=row)
        ))
        mock_session.commit = AsyncMock()
        fake_res = _FakeResources(mock_session)

        class _CM:
            async def __aenter__(self):
                return fake_res

            async def __aexit__(self, *args):
                return False

        # mock RAG 超时
        async def _slow_rag(**kwargs):
            await asyncio.sleep(2.0)
            return {}

        fake_ragas = {
            "summary": {"faithfulness": None, "answer_relevancy": None,
                        "context_precision": None, "context_recall": None,
                        "overall_score": None},
            "details": [],
            "metric_backend": "ragas-0.2",
            "error": None,
        }

        captured_samples = []

        async def _capture_ragas(*, samples, **kwargs):
            captured_samples.extend(samples)
            return fake_ragas

        with patch("app.rag.milvus_client.init_milvus", return_value=MagicMock()), \
             patch("app.tasks.eval_task.task_resources", return_value=_CM()), \
             patch("app.tasks.eval_task.get_settings") as mock_get_s, \
             patch("app.rag.eval_runner.run_single_query_for_eval",
                   new=AsyncMock(side_effect=_slow_rag)), \
             patch("app.rag.ragas_evaluator.evaluate_with_ragas",
                   new=AsyncMock(side_effect=_capture_ragas)):
            mock_get_s.return_value = SimpleNamespace(
                eval_question_timeout_s=0.1,  # 强制超时
                eval_llm_model=None,
                litellm_model="deepseek/x",
                litellm_api_key="k",
                litellm_api_base="https://api.deepseek.com",
                embedding_model="openai/y",
                embedding_api_key="ek",
                embedding_api_base="https://siliconflow.cn",
            )
            r = await _run_evaluation_main(str(eval_id))

        assert r["status"] == "completed"
        # 单题超时 → samples 仍占位，但 error 不为空
        assert len(captured_samples) == 1
        assert "timeout" in (captured_samples[0]["error"] or "")
        assert "超时" in captured_samples[0]["answer"]

    @pytest.mark.asyncio
    async def test_llm_config_missing_marks_failed(self):
        """eval_llm_model + litellm_model 都缺 → 提前 failed。"""
        from app.tasks.eval_task import _run_evaluation_main

        eval_id = uuid.uuid4()
        kb_id = uuid.uuid4()
        qa_set = [{"question": "q1", "ground_truth": "a1"}]
        row = _make_eval_task_row(eval_id, qa_set=qa_set, kb_id=kb_id)

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=row)
        ))
        mock_session.commit = AsyncMock()
        fake_res = _FakeResources(mock_session)

        class _CM:
            async def __aenter__(self):
                return fake_res

            async def __aexit__(self, *args):
                return False

        fake_rag_result = {"answer": "x", "contexts": [],
                           "source_citations": [], "error": None}

        with patch("app.rag.milvus_client.init_milvus", return_value=MagicMock()), \
             patch("app.tasks.eval_task.task_resources", return_value=_CM()), \
             patch("app.tasks.eval_task.get_settings") as mock_get_s, \
             patch("app.rag.eval_runner.run_single_query_for_eval",
                   new=AsyncMock(return_value=fake_rag_result)):
            mock_get_s.return_value = SimpleNamespace(
                eval_question_timeout_s=10.0,
                eval_llm_model=None,
                litellm_model=None,  # ← 缺！
                litellm_api_key=None,
                litellm_api_base=None,
                embedding_model=None,
                embedding_api_key=None,
                embedding_api_base=None,
            )
            r = await _run_evaluation_main(str(eval_id))

        assert r["status"] == "failed"
        assert "EVAL_LLM_MODEL" in r["reason"]


# ════════════════════════════════════════════════════════════════
# 7. API endpoints
# ════════════════════════════════════════════════════════════════


class TestEvaluationsEndpoints:
    @pytest.mark.asyncio
    async def test_create_rejects_empty_eval_set(self):
        """POST 评估集为空 → BusinessError(EVAL_DATASET_EMPTY=40012)。"""
        from app.api import error_codes
        from app.api.exceptions import BusinessError
        from app.api.v2.endpoints.evaluations import create_evaluation
        from app.schemas.v2.eval import EvalCreateRequest

        body = EvalCreateRequest(eval_set=[])
        mock_db = AsyncMock()

        with patch("app.api.v2.endpoints.evaluations.get_kb_or_raise",
                   new=AsyncMock(return_value=MagicMock())):
            with pytest.raises(BusinessError) as ei:
                await create_evaluation(kb_id=uuid.uuid4(), body=body, db=mock_db)
        assert ei.value.code == error_codes.EVAL_DATASET_EMPTY

    @pytest.mark.asyncio
    async def test_create_rejects_too_large_eval_set(self):
        """POST 超 eval_max_questions → 40013。"""
        from app.api import error_codes
        from app.api.exceptions import BusinessError
        from app.api.v2.endpoints.evaluations import create_evaluation
        from app.schemas.v2.eval import EvalCreateRequest, EvalQAItem

        # 用 100 题（默认 eval_max_questions=100），再加 1 题
        eval_set = [EvalQAItem(question=f"q{i}", ground_truth=f"a{i}")
                    for i in range(101)]
        body = EvalCreateRequest(eval_set=eval_set)
        mock_db = AsyncMock()

        with patch("app.api.v2.endpoints.evaluations.get_kb_or_raise",
                   new=AsyncMock(return_value=MagicMock())), \
             patch("app.api.v2.endpoints.evaluations.get_settings") as mock_s:
            mock_s.return_value = SimpleNamespace(
                eval_max_questions=100, eval_llm_model=None,
                litellm_model="x", embedding_model="y",
            )
            with pytest.raises(BusinessError) as ei:
                await create_evaluation(kb_id=uuid.uuid4(), body=body, db=mock_db)
        assert ei.value.code == error_codes.EVAL_DATASET_TOO_LARGE

    @pytest.mark.asyncio
    async def test_create_happy_path_returns_eval_task_id(self):
        """POST 正常 → 返 eval_task_id + status=pending；调用 .delay。"""
        from app.api.v2.endpoints.evaluations import create_evaluation
        from app.schemas.v2.eval import EvalCreateRequest, EvalQAItem

        body = EvalCreateRequest(
            eval_set=[EvalQAItem(question="q1", ground_truth="a1")],
            name="my-eval",
        )
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        # mock refresh 给 eval_task 加一个 id
        async def _refresh(obj):
            obj.id = uuid.uuid4()

        mock_db.refresh = AsyncMock(side_effect=_refresh)

        # mock celery .delay
        mock_delay = MagicMock()

        with patch("app.api.v2.endpoints.evaluations.get_kb_or_raise",
                   new=AsyncMock(return_value=MagicMock())), \
             patch("app.api.v2.endpoints.evaluations.get_settings") as mock_s, \
             patch("app.tasks.eval_task.run_evaluation_task") as mock_task:
            mock_s.return_value = SimpleNamespace(
                eval_max_questions=100, eval_llm_model="deepseek/x",
                litellm_model="deepseek/x", embedding_model="openai/y",
            )
            mock_task.delay = mock_delay

            resp = await create_evaluation(
                kb_id=uuid.uuid4(), body=body, db=mock_db,
            )

        assert resp.eval_task_id is not None
        assert resp.status == "pending"
        mock_delay.assert_called_once()
        # 确认有 EvalTask 被 add
        mock_db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_eval_404_when_missing(self):
        """GET 不存在的 eval_task_id → BusinessError(NOT_FOUND)。"""
        from app.api import error_codes
        from app.api.exceptions import BusinessError
        from app.api.v2.endpoints.evaluations import get_evaluation

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        ))

        with patch("app.api.v2.endpoints.evaluations.get_kb_or_raise",
                   new=AsyncMock(return_value=MagicMock())):
            with pytest.raises(BusinessError) as ei:
                await get_evaluation(
                    kb_id=uuid.uuid4(), eval_task_id=uuid.uuid4(), db=mock_db,
                )
        assert ei.value.code == error_codes.NOT_FOUND

    @pytest.mark.asyncio
    async def test_get_eval_returns_detail_when_completed(self):
        """GET 已完成的 eval_task → 返 summary/details + retrieval_options。"""
        from app.api.v2.endpoints.evaluations import get_evaluation

        eval_id = uuid.uuid4()
        kb_id = uuid.uuid4()
        row = MagicMock()
        row.id = eval_id
        row.kb_id = kb_id
        row.name = "test"
        row.status = "completed"
        row.progress = 100
        row.question_count = 1
        row.error_message = None
        row.created_at = datetime.now(timezone.utc)
        row.completed_at = datetime.now(timezone.utc)
        row.eval_result = {
            "summary": {"faithfulness": 0.9, "answer_relevancy": 0.8,
                        "context_precision": 0.7, "context_recall": 1.0,
                        "overall_score": 0.85},
            "details": [{"question": "q1", "ground_truth": "a1",
                         "answer": "x", "contexts": ["c"],
                         "faithfulness": 0.9, "answer_relevancy": 0.8,
                         "context_precision": 0.7, "context_recall": 1.0}],
        }
        row.eval_config = {"retrieval_options": {"top_k": 5}}

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=row)
        ))

        with patch("app.api.v2.endpoints.evaluations.get_kb_or_raise",
                   new=AsyncMock(return_value=MagicMock())):
            resp = await get_evaluation(
                kb_id=kb_id, eval_task_id=eval_id, db=mock_db,
            )

        assert resp.status == "completed"
        assert resp.summary.overall_score == 0.85
        assert len(resp.details) == 1
        assert resp.retrieval_options == {"top_k": 5}

    @pytest.mark.asyncio
    async def test_get_eval_summary_clamps_invalid_values(self):
        """eval_result 含越界 / 非数 → summary 字段置 None 不抛错。"""
        from app.api.v2.endpoints.evaluations import _extract_summary

        row = MagicMock()
        row.eval_result = {
            "summary": {"faithfulness": 1.5, "answer_relevancy": "abc",
                        "context_precision": 0.7, "context_recall": None,
                        "overall_score": 0.8},
        }
        s = _extract_summary(row)
        assert s.faithfulness is None       # 越界
        assert s.answer_relevancy is None   # 非数
        assert s.context_precision == 0.7
        assert s.context_recall is None
        assert s.overall_score == 0.8

    @pytest.mark.asyncio
    async def test_list_paginates_and_orders_desc(self):
        """GET 列表：分页 + 按 created_at 倒序。"""
        from app.api.v2.endpoints.evaluations import list_evaluations

        kb_id = uuid.uuid4()
        rows = []
        for i in range(3):
            m = MagicMock(spec=[])  # 空 spec 避免 MagicMock 默认属性误判
            m.id = uuid.uuid4()
            m.kb_id = kb_id
            m.name = f"n{i}"
            m.status = "completed"
            m.progress = 100
            m.question_count = 1
            m.eval_result = {"summary": {"faithfulness": 0.5}}
            m.eval_config = {"retrieval_options": {"top_k": 3}}
            m.created_at = datetime.now(timezone.utc)
            m.completed_at = datetime.now(timezone.utc)
            rows.append(m)

        mock_db = AsyncMock()
        # 两次 execute：第一次 count，第二次取 list
        count_result = MagicMock(scalar=MagicMock(return_value=3))
        list_result = MagicMock(scalars=MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=rows))
        ))
        mock_db.execute = AsyncMock(side_effect=[count_result, list_result])

        with patch("app.api.v2.endpoints.evaluations.get_kb_or_raise",
                   new=AsyncMock(return_value=MagicMock())):
            resp = await list_evaluations(
                kb_id=kb_id, page=1, page_size=20, db=mock_db,
            )

        assert resp.total == 3
        assert resp.page == 1
        assert len(resp.items) == 3
        assert resp.items[0].name == "n0"

    @pytest.mark.asyncio
    async def test_create_celery_unavailable_returns_error(self):
        """celery .delay 抛错 → CELERY_UNAVAILABLE(50300)。"""
        from app.api import error_codes
        from app.api.exceptions import BusinessError
        from app.api.v2.endpoints.evaluations import create_evaluation
        from app.schemas.v2.eval import EvalCreateRequest, EvalQAItem

        body = EvalCreateRequest(
            eval_set=[EvalQAItem(question="q", ground_truth="a")],
        )
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        async def _refresh(obj):
            obj.id = uuid.uuid4()
        mock_db.refresh = AsyncMock(side_effect=_refresh)

        with patch("app.api.v2.endpoints.evaluations.get_kb_or_raise",
                   new=AsyncMock(return_value=MagicMock())), \
             patch("app.api.v2.endpoints.evaluations.get_settings") as mock_s, \
             patch("app.tasks.eval_task.run_evaluation_task") as mock_task:
            mock_s.return_value = SimpleNamespace(
                eval_max_questions=100, eval_llm_model=None,
                litellm_model="x", embedding_model="y",
            )
            mock_task.delay = MagicMock(side_effect=RuntimeError("redis down"))

            with pytest.raises(BusinessError) as ei:
                await create_evaluation(
                    kb_id=uuid.uuid4(), body=body, db=mock_db,
                )
        assert ei.value.code == error_codes.CELERY_UNAVAILABLE


# ════════════════════════════════════════════════════════════════
# 8. router 路径注册
# ════════════════════════════════════════════════════════════════


class TestEvaluationRouterRegistration:
    def test_all_three_paths_registered(self):
        """3 个 EVA 路径都挂到 /api/v2 前缀下。"""
        from app.api.v2.router import router

        paths = {r.path for r in router.routes}
        assert "/api/v2/knowledge-bases/{kb_id}/evaluate" in paths
        assert "/api/v2/knowledge-bases/{kb_id}/evaluations" in paths
        assert "/api/v2/knowledge-bases/{kb_id}/evaluations/{eval_task_id}" in paths

    def test_main_includes_eval_endpoints(self):
        """main.create_app 后 OpenAPI 能看到 EVA 端点。"""
        from app.main import create_app

        app = create_app()
        eva_routes = [
            r.path for r in app.routes
            if hasattr(r, "path") and "evaluat" in r.path
        ]
        # 至少看到 3 条评估相关路由
        assert len(eva_routes) >= 3


# ════════════════════════════════════════════════════════════════
# 9. 错误码注册
# ════════════════════════════════════════════════════════════════


class TestEvalErrorCodes:
    def test_eval_error_codes_defined(self):
        """新增的 40012 / 40013 已在 error_codes + DEFAULT_MESSAGES + HTTP 映射就位。"""
        from app.api import error_codes
        from app.api.exceptions import HTTP_STATUS_BY_CODE

        assert error_codes.EVAL_DATASET_EMPTY == 40012
        assert error_codes.EVAL_DATASET_TOO_LARGE == 40013
        assert error_codes.EVAL_DATASET_EMPTY in error_codes.DEFAULT_MESSAGES
        assert error_codes.EVAL_DATASET_TOO_LARGE in error_codes.DEFAULT_MESSAGES
        assert HTTP_STATUS_BY_CODE[error_codes.EVAL_DATASET_EMPTY] == 400
        assert HTTP_STATUS_BY_CODE[error_codes.EVAL_DATASET_TOO_LARGE] == 400


# ════════════════════════════════════════════════════════════════
# 10. similarity_threshold 运行时覆盖（A.1 调优支持）
# ════════════════════════════════════════════════════════════════


class TestSimilarityThresholdOverride:
    """验证 similarity_threshold 从 eval_runner → hybrid_search → reranker 的透传。"""

    @pytest.mark.asyncio
    async def test_eval_runner_passes_threshold_to_hybrid_search(self):
        """eval_runner 把 resolved.similarity_threshold 透传给 hybrid_search。"""
        from app.rag.eval_runner import run_single_query_for_eval
        from app.rag.hybrid_retriever import HybridSearchResult

        # 捕获 hybrid_search 调用参数
        captured_kwargs = {}

        async def _capture_hybrid_search(**kwargs):
            captured_kwargs.update(kwargs)
            return [HybridSearchResult(chunk_id=1, content="c", score=0.9)]

        with patch("app.rag.eval_runner.hybrid_search",
                   new=_capture_hybrid_search), \
             patch("app.rag.eval_runner.generate_answer",
                   new=AsyncMock(return_value="answer")), \
             patch("app.rag.eval_runner.rewrite_query",
                   new=AsyncMock(side_effect=lambda q, s: __import__(
                       "app.rag.query_rewriter", fromlist=["RewriteResult"]
                   ).RewriteResult())), \
             patch("app.rag.eval_runner.extract_query_entities",
                   new=AsyncMock(return_value=[])), \
             patch("app.rag.eval_runner.anchor_to_graph",
                   new=AsyncMock(return_value=[])):
            mock_db = AsyncMock()
            mock_db.get = AsyncMock(return_value=None)
            await run_single_query_for_eval(
                query="q",
                kb_ids=[uuid.uuid4()],
                options={"similarity_threshold": 0.1},
                db=mock_db,
            )

        # hybrid_search 应该收到 similarity_threshold=0.1
        assert captured_kwargs.get("similarity_threshold") == 0.1

    def test_reranker_uses_override_threshold(self):
        """SiliconFlowReranker 构造时传 similarity_threshold → 覆盖 settings 值。"""
        from app.rag.reranker import SiliconFlowReranker

        with patch("app.rag.reranker.get_settings") as mock_get:
            mock_get.return_value = SimpleNamespace(
                reranker_model="test/model",
                reranker_api_key="k",
                reranker_api_base="https://api.siliconflow.cn/v1",
                reranker_similarity_threshold=0.3,  # settings 里是 0.3
                litellm_timeout=30.0,
            )
            reranker = SiliconFlowReranker(similarity_threshold=0.0)

        assert reranker.similarity_threshold == 0.0  # 运行时覆盖生效

    def test_reranker_falls_back_to_settings_when_no_override(self):
        """不传 similarity_threshold → 用 settings 全局值。"""
        from app.rag.reranker import SiliconFlowReranker

        with patch("app.rag.reranker.get_settings") as mock_get:
            mock_get.return_value = SimpleNamespace(
                reranker_model="test/model",
                reranker_api_key="k",
                reranker_api_base="https://api.siliconflow.cn/v1",
                reranker_similarity_threshold=0.3,
                litellm_timeout=30.0,
            )
            reranker = SiliconFlowReranker()  # 不传

        assert reranker.similarity_threshold == 0.3  # 回落到 settings

    def test_get_reranker_passes_threshold_to_siliconflow(self):
        """get_reranker(similarity_threshold=0.1) → SiliconFlowReranker 阈值为 0.1。"""
        from app.rag.reranker import get_reranker

        with patch("app.rag.reranker.get_settings") as mock_get:
            mock_get.return_value = SimpleNamespace(
                reranker_type="api",
                reranker_model="test/model",
                reranker_api_key="k",
                reranker_api_base="https://api.siliconflow.cn/v1",
                reranker_similarity_threshold=0.3,
                litellm_timeout=30.0,
            )
            reranker = get_reranker(similarity_threshold=0.1)

        assert reranker.similarity_threshold == 0.1
