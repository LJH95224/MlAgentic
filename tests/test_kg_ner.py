"""NER 单测：mock LLM，验证 JSON 解析、去重、软失败行为。"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.kg.ner import _parse_entities, run_ner


# ──────────────────── _parse_entities 纯逻辑 ────────────────────


class TestParseEntities:
    def test_normal_json(self):
        content = json.dumps(
            {
                "entities": [
                    {"name": "北京", "type": "LOCATION"},
                    {"name": "中国气象局", "type": "ORG"},
                ]
            },
            ensure_ascii=False,
        )
        result = _parse_entities(content)
        assert len(result) == 2
        assert result[0] == {"name": "北京", "type": "LOCATION"}
        assert result[1] == {"name": "中国气象局", "type": "ORG"}

    def test_duplicate_entities_dedup(self):
        """同一 (name, type) 重复时只保留一次。"""
        content = json.dumps(
            {
                "entities": [
                    {"name": "北京", "type": "LOCATION"},
                    {"name": "北京", "type": "LOCATION"},
                    {"name": "上海", "type": "LOCATION"},
                ]
            },
            ensure_ascii=False,
        )
        result = _parse_entities(content)
        assert len(result) == 2

    def test_same_name_different_type_kept(self):
        """同名不同类型应被保留（如"苹果" ORG vs FRUIT）。"""
        content = json.dumps(
            {
                "entities": [
                    {"name": "苹果", "type": "ORG"},
                    {"name": "苹果", "type": "OTHER"},
                ]
            },
            ensure_ascii=False,
        )
        result = _parse_entities(content)
        assert len(result) == 2

    def test_markdown_fence_stripped(self):
        """LLM 偶尔会用 ```json 围栏，解析时要剥掉。"""
        content = '```json\n{"entities": [{"name": "x", "type": "OTHER"}]}\n```'
        result = _parse_entities(content)
        assert len(result) == 1
        assert result[0]["name"] == "x"

    def test_invalid_type_normalized_to_other(self):
        """不在白名单的 type 归并到 OTHER。"""
        content = json.dumps(
            {"entities": [{"name": "x", "type": "WEIRD"}]},
            ensure_ascii=False,
        )
        result = _parse_entities(content)
        assert result[0]["type"] == "OTHER"

    def test_type_uppercased(self):
        """type 大小写不敏感，统一归一为大写。"""
        content = json.dumps(
            {"entities": [{"name": "x", "type": "person"}]},
            ensure_ascii=False,
        )
        result = _parse_entities(content)
        assert result[0]["type"] == "PERSON"

    def test_empty_entities_returns_empty(self):
        content = json.dumps({"entities": []}, ensure_ascii=False)
        assert _parse_entities(content) == []

    def test_missing_name_or_type_skipped(self):
        """缺 name 或 type 的条目被跳过，不抛错。"""
        content = json.dumps(
            {
                "entities": [
                    {"name": "ok", "type": "PERSON"},
                    {"name": ""},
                    {"type": "PERSON"},
                    "not_a_dict",
                ]
            },
            ensure_ascii=False,
        )
        result = _parse_entities(content)
        assert len(result) == 1
        assert result[0]["name"] == "ok"


# ──────────────────── run_ner 端到端（mock LLM） ────────────────────


@pytest.fixture
def mock_settings(monkeypatch):
    """mock settings：填上必需的 LLM 配置。"""
    from app.core import config

    fake = MagicMock()
    fake.kg_ner_model = None
    fake.litellm_model = "deepseek/deepseek-chat"
    fake.litellm_api_key = "fake-key"
    fake.litellm_api_base = "https://api.deepseek.com"
    fake.litellm_timeout = 30.0
    fake.litellm_num_retries = 1

    monkeypatch.setattr(config, "get_settings", lambda: fake)
    monkeypatch.setattr("app.kg.ner.get_settings", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_run_ner_returns_parsed_entities(mock_settings, monkeypatch):
    """正常流程：mock litellm.acompletion 返回标准 JSON。"""
    fake_resp = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {"entities": [{"name": "北京", "type": "LOCATION"}]},
                        ensure_ascii=False,
                    )
                }
            }
        ]
    }
    mock_completion = AsyncMock(return_value=fake_resp)
    monkeypatch.setattr("app.kg.ner.litellm.acompletion", mock_completion)

    result = await run_ner("北京今天天气晴")
    assert len(result) == 1
    assert result[0]["name"] == "北京"


@pytest.mark.asyncio
async def test_run_ner_returns_empty_on_json_failure(mock_settings, monkeypatch):
    """LLM 返回非 JSON 时软降级为空列表，不抛错。"""
    fake_resp = {
        "choices": [{"message": {"content": "这不是 JSON"}}]
    }
    mock_completion = AsyncMock(return_value=fake_resp)
    monkeypatch.setattr("app.kg.ner.litellm.acompletion", mock_completion)

    result = await run_ner("文本")
    assert result == []


@pytest.mark.asyncio
async def test_run_ner_returns_empty_on_llm_exception(mock_settings, monkeypatch):
    """LLM 抛异常（限流/网络抖动）时软降级为空列表，不传播。"""
    mock_completion = AsyncMock(side_effect=RuntimeError("rate limit"))
    monkeypatch.setattr("app.kg.ner.litellm.acompletion", mock_completion)

    result = await run_ner("文本")
    assert result == []


@pytest.mark.asyncio
async def test_run_ner_empty_input_returns_empty(mock_settings):
    """空文本 / 仅空白：直接返回 []，不调 LLM。"""
    assert await run_ner("") == []
    assert await run_ner("   \n\t  ") == []


@pytest.mark.asyncio
async def test_run_ner_uses_kg_ner_model_if_set(mock_settings, monkeypatch):
    """若 KG_NER_MODEL 配了，优先使用，不复用 LITELLM_MODEL。"""
    mock_settings.kg_ner_model = "openai/cheap-ner-model"

    captured_kwargs = {}

    async def fake_acompletion(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "choices": [
                {"message": {"content": json.dumps({"entities": []})}}
            ]
        }

    monkeypatch.setattr("app.kg.ner.litellm.acompletion", fake_acompletion)

    await run_ner("文本")
    assert captured_kwargs["model"] == "openai/cheap-ner-model"
