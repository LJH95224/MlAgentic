"""Embedding 客户端单测（不连真 LiteLLM / 远端 API）。

覆盖 [app/rag/embedding.py](../app/rag/embedding.py) 的两个公开入口：

- ``_build_kwargs(input_texts)``：纯函数，校验 kwargs 拼装
- ``aembed_texts(texts)``：主流程，覆盖维度严格校验 / 乱序排序 / 异常透传 / 兜底分支

设计原则与项目其它 LiteLLM 测试一致——
1. 全部 mock ``litellm.aembedding``，零外部依赖
2. settings 通过 ``monkeypatch`` 注入字段 + ``get_settings.cache_clear()``
3. 既测 happy path 也测每个 ValueError 分支
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import get_settings


# ───────────────────────── 公共 fixture ─────────────────────────


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """每个 case 跑完清掉 get_settings LRU，避免污染其它测试模块。"""
    yield
    get_settings.cache_clear()


@pytest.fixture
def _inject_embedding_settings(monkeypatch):
    """注入一组最小可用的 Embedding 配置 + Settings 缓存清理。

    返回一个 setter 函数，方便每个 case 微调单字段。
    """

    def _apply(**overrides):
        settings = get_settings()
        # 默认值（足够走通 happy path）
        defaults = {
            "embedding_model": "openai/Qwen/Qwen3-Embedding-8B",
            "embedding_api_key": "sk-fake-key",
            "embedding_api_base": "https://api.siliconflow.cn/v1",
            "embedding_dimension": 4,
            "litellm_timeout": 12,
        }
        defaults.update(overrides)
        for k, v in defaults.items():
            monkeypatch.setattr(settings, k, v, raising=True)
        return settings

    return _apply


def _make_openai_resp(vectors: list[list[float]], *, total_tokens: int = 100) -> dict:
    """构造 OpenAI 标准 Embedding 响应 dict。"""
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "embedding": vec, "index": i}
            for i, vec in enumerate(vectors)
        ],
        "model": "fake-embedding",
        "usage": {"prompt_tokens": total_tokens, "total_tokens": total_tokens},
    }


# ───────────────────────── _build_kwargs ─────────────────────────


class TestBuildKwargs:
    """``_build_kwargs`` 是 ``aembed_texts`` 的拼装层，单独测保证契约稳定。"""

    def test_raises_when_model_not_configured(self, _inject_embedding_settings):
        """EMBEDDING_MODEL 未配置时直接抛 ValueError，文案带配置示例。"""
        from app.rag.embedding import _build_kwargs

        _inject_embedding_settings(embedding_model=None)
        with pytest.raises(ValueError, match="EMBEDDING_MODEL"):
            _build_kwargs(["hello"])

    def test_includes_model_input_timeout(self, _inject_embedding_settings):
        """三个核心字段必传：model / input / timeout（timeout 取 litellm_timeout）。"""
        from app.rag.embedding import _build_kwargs

        _inject_embedding_settings(litellm_timeout=33)
        kwargs = _build_kwargs(["a", "b", "c"])
        assert kwargs["model"] == "openai/Qwen/Qwen3-Embedding-8B"
        assert kwargs["input"] == ["a", "b", "c"]
        assert kwargs["timeout"] == 33

    def test_optional_keys_present_when_configured(self, _inject_embedding_settings):
        """api_key / api_base 配置后必须出现在 kwargs 中。"""
        from app.rag.embedding import _build_kwargs

        _inject_embedding_settings(
            embedding_api_key="sk-prod", embedding_api_base="https://api.x.com/v1"
        )
        kwargs = _build_kwargs(["q"])
        assert kwargs["api_key"] == "sk-prod"
        assert kwargs["api_base"] == "https://api.x.com/v1"

    def test_optional_keys_absent_when_unset(self, _inject_embedding_settings):
        """api_key / api_base 未配置时不出现在 kwargs（不写空字符串占位）。"""
        from app.rag.embedding import _build_kwargs

        _inject_embedding_settings(embedding_api_key=None, embedding_api_base=None)
        kwargs = _build_kwargs(["q"])
        assert "api_key" not in kwargs
        assert "api_base" not in kwargs

    def test_never_passes_dimensions(self, _inject_embedding_settings):
        """关键契约：永远不传 ``dimensions``（LiteLLM openai/ 路由会拒）。

        即便 settings.embedding_dimension=4096 也不能透传出去；维度由模型决定，
        校验在 aembed_texts 内做。
        """
        from app.rag.embedding import _build_kwargs

        _inject_embedding_settings(embedding_dimension=4096)
        kwargs = _build_kwargs(["q"])
        assert "dimensions" not in kwargs, (
            "EMBEDDING_DIMENSION 不应作为 dimensions 参数透传给 LiteLLM"
        )


# ───────────────────────── aembed_texts happy path ─────────────────────────


class TestAembedTextsHappyPath:
    """正常路径与基础校验。"""

    @pytest.mark.asyncio
    async def test_returns_vectors_in_input_order(
        self, _inject_embedding_settings, monkeypatch
    ):
        """正常返回：每条 input 对应一个 4 维 vector，顺序与输入一致。"""
        from app.rag import embedding as emb

        _inject_embedding_settings(embedding_dimension=4)
        resp = _make_openai_resp([
            [0.1, 0.2, 0.3, 0.4],
            [0.5, 0.6, 0.7, 0.8],
        ])
        monkeypatch.setattr(emb.litellm, "aembedding", AsyncMock(return_value=resp))

        out = await emb.aembed_texts(["hello", "world"])
        assert out == [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]]

    @pytest.mark.asyncio
    async def test_reorders_by_index_field(
        self, _inject_embedding_settings, monkeypatch
    ):
        """部分厂商可能乱序返回 data；必须按 index 字段升序排序。"""
        from app.rag import embedding as emb

        _inject_embedding_settings(embedding_dimension=3)
        # 故意把 index=1 排在 index=0 之前
        resp = {
            "data": [
                {"embedding": [0.9, 0.9, 0.9], "index": 1},
                {"embedding": [0.1, 0.1, 0.1], "index": 0},
            ],
            "usage": {"total_tokens": 12},
        }
        monkeypatch.setattr(emb.litellm, "aembedding", AsyncMock(return_value=resp))

        out = await emb.aembed_texts(["first", "second"])
        # index=0 → first → [0.1, ...]；index=1 → second → [0.9, ...]
        assert out[0] == [0.1, 0.1, 0.1]
        assert out[1] == [0.9, 0.9, 0.9]

    @pytest.mark.asyncio
    async def test_accepts_pydantic_response_via_model_dump(
        self, _inject_embedding_settings, monkeypatch
    ):
        """LiteLLM 可能返回 Pydantic 对象（有 model_dump），应能正确转 dict。"""
        from app.rag import embedding as emb

        _inject_embedding_settings(embedding_dimension=2)
        # 用 MagicMock 模拟 Pydantic 对象：仅暴露 model_dump
        fake_resp = MagicMock(spec=["model_dump"])
        fake_resp.model_dump.return_value = _make_openai_resp([[1.0, 2.0]])
        monkeypatch.setattr(
            emb.litellm, "aembedding", AsyncMock(return_value=fake_resp)
        )

        out = await emb.aembed_texts(["pydantic-shaped"])
        assert out == [[1.0, 2.0]]
        fake_resp.model_dump.assert_called_once()


# ───────────────────────── aembed_texts 异常分支 ─────────────────────────


class TestAembedTextsValidation:
    """入参校验与维度校验路径。"""

    @pytest.mark.asyncio
    async def test_empty_input_raises_value_error(self):
        """texts=[] 直接 ValueError，不发任何远端调用。"""
        from app.rag.embedding import aembed_texts

        with pytest.raises(ValueError, match="不能为空"):
            await aembed_texts([])

    @pytest.mark.asyncio
    async def test_count_mismatch_raises(
        self, _inject_embedding_settings, monkeypatch
    ):
        """LiteLLM 返回条数 ≠ 输入条数时必须立即抛错，防止半成品入 Milvus。"""
        from app.rag import embedding as emb

        _inject_embedding_settings(embedding_dimension=2)
        # 只返一条但输入两条
        resp = _make_openai_resp([[0.1, 0.2]])
        monkeypatch.setattr(emb.litellm, "aembedding", AsyncMock(return_value=resp))

        with pytest.raises(ValueError, match="返回条数与输入不一致"):
            await emb.aembed_texts(["a", "b"])

    @pytest.mark.asyncio
    async def test_dimension_mismatch_raises(
        self, _inject_embedding_settings, monkeypatch
    ):
        """单条向量维度与 EMBEDDING_DIMENSION 不一致 → ValueError 含 index/expected/got。"""
        from app.rag import embedding as emb

        _inject_embedding_settings(embedding_dimension=4)
        # 第二条只有 3 维
        resp = _make_openai_resp([[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7]])
        monkeypatch.setattr(emb.litellm, "aembedding", AsyncMock(return_value=resp))

        with pytest.raises(ValueError, match="维度不匹配"):
            await emb.aembed_texts(["a", "b"])

    @pytest.mark.asyncio
    async def test_litellm_exception_propagates(
        self, _inject_embedding_settings, monkeypatch
    ):
        """LiteLLM 抛错时不吞掉，原样透传给调用方决策（docstring 明确契约）。"""
        from app.rag import embedding as emb

        _inject_embedding_settings()

        class FakeRateLimitError(Exception):
            pass

        monkeypatch.setattr(
            emb.litellm,
            "aembedding",
            AsyncMock(side_effect=FakeRateLimitError("429 rate limited")),
        )

        with pytest.raises(FakeRateLimitError, match="429"):
            await emb.aembed_texts(["a"])

    @pytest.mark.asyncio
    async def test_missing_data_key_raises(
        self, _inject_embedding_settings, monkeypatch
    ):
        """响应里 data 缺失（异常 API 实现） → 当成空列表 → 条数校验抛错。"""
        from app.rag import embedding as emb

        _inject_embedding_settings(embedding_dimension=2)
        monkeypatch.setattr(
            emb.litellm,
            "aembedding",
            AsyncMock(return_value={"usage": {"total_tokens": 0}}),
        )

        with pytest.raises(ValueError, match="返回条数"):
            await emb.aembed_texts(["a"])


__all__: list[str] = []
