"""真 LLM 联调脚本（手动运行，不在 pytest 中自动执行）。

用途：
  - 验证 LiteLLM + DeepSeek 的端到端连通性
  - 验证 Function Calling（tool_calls）能力（LLM-02 验收）

运行方式：
  conda activate geo_agent
  cd TyAgent（项目根目录）
  python scripts/llm_smoke.py

前置条件：
  .env 中已配置 LITELLM_MODEL / LITELLM_API_KEY / LITELLM_API_BASE
"""

import asyncio
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# 确保项目根目录在 sys.path 中，这样 `from app.xxx import ...` 才能生效
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app.llm import acompletion, astream, define_tool, user


async def test_1_text_completion():
    """测试 1：纯文本对话。"""
    print("=" * 50)
    print("测试 1：纯文本对话")
    print("=" * 50)

    resp = await acompletion(messages=[user("用一句话介绍北京")])
    content = resp["choices"][0]["message"]["content"]
    usage = resp.get("usage", {})
    print(f"回复: {content}")
    print(f"Token: prompt={usage.get('prompt_tokens')} completion={usage.get('completion_tokens')} total={usage.get('total_tokens')}")
    print()


async def test_2_tool_call():
    """测试 2：Function Calling（LLM-02 验收核心）。

    注册一个 get_weather 工具，让模型主动调用它，验证：
    - finish_reason == "tool_calls"
    - tool_calls[0].function.name == "get_weather"
    - arguments 是合法 JSON 且包含模型推断的参数
    """
    print("=" * 50)
    print("测试 2：Function Calling")
    print("=" * 50)

    weather_tool = define_tool(
        name="get_weather",
        description="获取指定城市的当前天气信息",
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名称"},
            },
            "required": ["city"],
        },
    )

    resp = await acompletion(
        messages=[user("上海今天天气怎么样？")],
        tools=[weather_tool],
    )

    choice = resp["choices"][0]
    msg = choice["message"]
    print(f"finish_reason: {choice['finish_reason']}")
    print(f"content: {msg.get('content')}")

    # client.py 已统一将 ModelResponse 转为 dict，用 dict 风格访问即可
    tool_calls = msg.get("tool_calls")
    if tool_calls:
        for tc in tool_calls:
            fn = tc["function"]
            print(f"tool_call: name={fn['name']} arguments={fn['arguments']}")
            args = json.loads(fn["arguments"])
            assert fn["name"] == "get_weather", f"期望 get_weather，实际 {fn['name']}"
            assert "city" in args, f"期望参数含 city，实际 {args}"
            print(f"  -> 解析后: city={args['city']}")
    else:
        print("⚠️  模型未触发 tool_calls，可能是模型版本不支持或配置问题")

    print()


async def test_3_stream():
    """测试 3：流式输出。"""
    print("=" * 50)
    print("测试 3：流式输出")
    print("=" * 50)

    full_text = []
    async for chunk in astream(messages=[user("数到5，每行一个数")]):
        delta = chunk["choices"][0]["delta"]
        content = delta.get("content", "")
        if content:
            print(content, end="", flush=True)
            full_text.append(content)

    print()
    print(f"[流式完成，共 {len(full_text)} 个 chunk]")
    print()


async def main():
    print("TyAgent LLM 联调脚本")
    print()

    from app.core.config import get_settings

    s = get_settings()
    print(f"模型配置: LITELLM_MODEL={s.litellm_model}")
    print(f"API Base: {s.litellm_api_base}")
    print(f"API Key:  {s.litellm_api_key[:8]}..." if s.litellm_api_key else "API Key: (未设置)")
    print()

    try:
        await test_1_text_completion()
        await test_2_tool_call()
        await test_3_stream()
        print("=" * 50)
        print("全部测试通过！")
    except Exception as e:
        print(f"\n测试失败: {type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
