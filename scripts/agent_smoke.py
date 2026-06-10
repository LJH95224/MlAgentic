"""真 LLM + LangGraph ReAct 端到端联调脚本（手动运行，会烧 token）。

用途：
  - 验证 AGT-02: 完整的 Thought → Action → Observation → Final Answer 循环
  - 验证 LLM 能主动调用 mock_weather_parser
  - 验证 SSE 控制流（tool_start / tool_end）在真链路中正确发射

运行方式：
  conda activate geo_agent
  cd TyAgent
  python scripts/agent_smoke.py

前置条件：
  .env 中已配置 LITELLM_MODEL / LITELLM_API_KEY / LITELLM_API_BASE
"""

import asyncio
import sys
import uuid
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app.agent.runner import (
    AgentDone,
    AgentTextChunk,
    AgentToolEnd,
    AgentToolStart,
    run_stream,
)


async def run_one(prompt: str, history: list[dict] | None = None) -> None:
    """跑一次完整 Agent 流式输出，把事件打印到终端。"""
    print(f"\n>>> 用户: {prompt}")
    print("─" * 60)

    text_buffer: list[str] = []
    async for event in run_stream(uuid.uuid4(), prompt, history=history):
        if isinstance(event, AgentTextChunk):
            print(event.content, end="", flush=True)
            text_buffer.append(event.content)
        elif isinstance(event, AgentToolStart):
            print(f"\n[🔧 工具调用开始] {event.tool}", flush=True)
        elif isinstance(event, AgentToolEnd):
            output_preview = (event.output or "")[:100]
            print(f"[✅ 工具调用结束] {event.tool} -> {output_preview}", flush=True)
        elif isinstance(event, AgentDone):
            print(f"\n─ 完成（最终长度: {len(event.final_content)} 字符）─")


async def main():
    print("=" * 60)
    print("TyAgent 3.3 LangGraph 联调脚本")
    print("=" * 60)

    from app.core.config import get_settings

    s = get_settings()
    print(f"模型: {s.litellm_model}")
    print(f"API:  {s.litellm_api_base}")
    print(f"最大迭代: {s.agent_max_iterations}")

    # 用例 1：纯文本（不应触发工具）
    await run_one("用一句话介绍一下北京")

    # 用例 2：应触发 mock_weather_parser
    await run_one("查一下气象站 54511 在 2026-06-09 的天气数据")

    # 用例 3：多轮（带历史）
    history = [
        {"role": "user", "content": "我想了解气象站 58367"},
        {"role": "assistant", "content": "好的，请告诉我您想查询的日期。"},
    ]
    await run_one("查一下今天 2026-06-09 的数据", history=history)

    print("\n" + "=" * 60)
    print("联调结束")


if __name__ == "__main__":
    asyncio.run(main())
