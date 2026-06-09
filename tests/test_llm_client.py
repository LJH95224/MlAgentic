"""LLM client 单元测试（mock litellm，不依赖网络）。

覆盖点：
- 模型名自动前缀补全（LLM-01 配置切换的辅助能力）
- acompletion 消息透传与返回结构（LLM-02 基础）
- astream 流式 chunk 透传
- tool_calls 解析（LLM-02 验收：模型输出 tool_calls）
- LITELLM_MODEL 未配置时报错
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.llm import acompletion, astream, define_tool, user


# ────────────── 辅助：构造假响应 ──────────────


def _fake_text_response(text: str = "你好", model: str = "deepseek/deepseek-chat") -> dict:
    """构造一个 LiteLLM 规范的纯文本回复 dict。"""
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _fake_tool_call_response(tool_name: str, tool_args: dict, model: str = "deepseek/deepseek-chat") -> dict:
    """构造一个模型主动调用工具的回复 dict（LLM-02 验收核心）。"""
    return {
        "id": "chatcmpl-fake-tool",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_fake_001",
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(tool_args, ensure_ascii=False),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 15, "total_tokens": 35},
    }


# ────────────── 模型名前缀补全 ──────────────


class TestModelNameResolution:
    """LLM-01: 仅修改 .env 即可无缝切换厂商。"""

    def test_user_already_has_prefix(self, mock_env_vars):
        """用户写了带前缀的模型名，不应重复补。"""
        mock_env_vars(LITELLM_MODEL="deepseek/deepseek-chat", LITELLM_API_BASE="https://api.deepseek.com")
        from app.llm.client import _resolve_model_name

        assert _resolve_model_name("deepseek/deepseek-chat", "https://api.deepseek.com") == "deepseek/deepseek-chat"

    def test_auto_prefix_deepseek(self, mock_env_vars):
        """LITELLM_MODEL=deepseek-chat 自动补为 deepseek/deepseek-chat。"""
        mock_env_vars(LITELLM_MODEL="deepseek-chat", LITELLM_API_BASE="https://api.deepseek.com")
        from app.llm.client import _resolve_model_name

        assert _resolve_model_name("deepseek-chat", "https://api.deepseek.com") == "deepseek/deepseek-chat"

    def test_no_model_raises(self, mock_env_vars):
        """LITELLM_MODEL 未配置应抛 ValueError。"""
        mock_env_vars(LITELLM_MODEL="", LITELLM_API_BASE="https://api.deepseek.com")
        from app.llm.client import _resolve_model_name

        with pytest.raises(ValueError, match="LITELLM_MODEL"):
            _resolve_model_name(None, "https://api.deepseek.com")


# ────────────── acompletion ──────────────


class TestAcompletion:
    """一次性调用 LLM 的核心路径。"""

    @pytest.mark.asyncio
    async def test_text_response(self, mock_env_vars):
        """纯文本回复：content 有值，tool_calls 不存在。"""
        mock_env_vars(
            LITELLM_MODEL="deepseek/deepseek-chat",
            LITELLM_API_KEY="sk-fake",
            LITELLM_API_BASE="https://api.deepseek.com",
        )

        fake_resp = _fake_text_response("北京今天晴天")

        with patch("app.llm.client.litellm.acompletion", new_callable=AsyncMock) as mock_litellm:
            mock_litellm.return_value = fake_resp
            resp = await acompletion(messages=[user("北京天气")])

        assert resp["choices"][0]["message"]["content"] == "北京今天晴天"
        assert "tool_calls" not in resp["choices"][0]["message"]
        # 验证 litellm 收到了正确的 kwargs
        call_kwargs = mock_litellm.call_args[1]
        assert call_kwargs["model"] == "deepseek/deepseek-chat"
        assert call_kwargs["api_key"] == "sk-fake"
        assert call_kwargs["api_base"] == "https://api.deepseek.com"

    @pytest.mark.asyncio
    async def test_tool_call_response(self, mock_env_vars):
        """LLM-02 验收：模型主动输出 tool_calls。"""
        mock_env_vars(
            LITELLM_MODEL="deepseek/deepseek-chat",
            LITELLM_API_KEY="sk-fake",
            LITELLM_API_BASE="https://api.deepseek.com",
        )

        search_tool = define_tool(
            name="search_knowledge_base",
            description="检索知识库",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
                "required": ["query"],
            },
        )
        fake_resp = _fake_tool_call_response("search_knowledge_base", {"query": "雷达回波", "top_k": 3})

        with patch("app.llm.client.litellm.acompletion", new_callable=AsyncMock) as mock_litellm:
            mock_litellm.return_value = fake_resp
            resp = await acompletion(
                messages=[user("分析雷达回波")],
                tools=[search_tool],
            )

        choice = resp["choices"][0]
        assert choice["finish_reason"] == "tool_calls"
        assert len(choice["message"]["tool_calls"]) == 1
        tc = choice["message"]["tool_calls"][0]
        assert tc["function"]["name"] == "search_knowledge_base"
        args = json.loads(tc["function"]["arguments"])
        assert args["query"] == "雷达回波"
        assert args["top_k"] == 3
        # 验证 litellm 收到了 tools 参数
        call_kwargs = mock_litellm.call_args[1]
        assert call_kwargs["tools"] == [search_tool]


# ────────────── astream ──────────────


class TestAstream:
    """流式调用 LLM 的核心路径。"""

    @pytest.mark.asyncio
    async def test_stream_chunks(self, mock_env_vars):
        """流式应返回多个 chunk，每个含 delta.content。"""
        mock_env_vars(
            LITELLM_MODEL="deepseek/deepseek-chat",
            LITELLM_API_KEY="sk-fake",
        )

        # 构造假流：3 个文本 chunk + 1 个 finish chunk
        async def _fake_stream(**kwargs):
            chunks = [
                {"choices": [{"delta": {"role": "assistant", "content": "你"}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": "好"}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": "！"}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ]
            for c in chunks:
                yield c

        with patch("app.llm.client.litellm.acompletion", new_callable=AsyncMock) as mock_litellm:
            mock_litellm.return_value = _fake_stream()
            collected = []
            async for chunk in astream(messages=[user("hi")]):
                collected.append(chunk)

        # 至少 3 个文本 chunk
        text_chunks = [c for c in collected if c["choices"][0]["delta"].get("content")]
        assert len(text_chunks) == 3
        joined = "".join(c["choices"][0]["delta"]["content"] for c in text_chunks)
        assert joined == "你好！"
