"""流式对话接口测试（API-02 / API-03）。"""

import json
import uuid

from tests.conftest import skip_without_db


def _parse_sse_lines(text: str) -> list[dict]:
    """把 SSE 响应文本解析为事件列表 [{event, data}]。

    SSE 协议要求帧之间用空行分隔。sse-starlette 在每行末尾追加 CRLF，
    所以这里先把 \\r\\n 统一为 \\n，再按空行切分。
    """
    normalized = text.replace("\r\n", "\n").strip("\n")
    events: list[dict] = []
    for block in normalized.split("\n\n"):
        if not block.strip():
            continue
        evt: dict = {}
        for line in block.split("\n"):
            if not line or ":" not in line:
                continue
            field, _, value = line.partition(":")
            evt[field.strip()] = value.lstrip()
        if "data" in evt:
            evt["data"] = json.loads(evt["data"])
        events.append(evt)
    return events


@skip_without_db
async def test_chat_stream_full_flow(client):
    """完整链路：创建会话 → 发起流式对话 → 验证文本流 + done 事件。

    注意：3.3 之后 agent 走真实 LangGraph + LLM 推理，是否触发 tool_call
    取决于模型对提示词的判断，因此本测试只对"文本流必须存在"和"以 done 结尾"
    做硬断言；tool_start 事件作为软断言（如果出现则校验结构）。
    """
    # 1) 先建会话
    sess_resp = await client.post("/api/v1/sessions")
    session_id = sess_resp.json()["id"]

    # 2) 发起流式对话
    resp = await client.post(
        "/api/v1/chat/stream",
        json={"session_id": session_id, "content": "你好，测试一下"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse_lines(resp.text)
    assert len(events) > 0

    # 必须包含至少一个 text 事件（API-02 打字机效果）
    text_events = [e for e in events if e["data"].get("type") == "text"]
    assert len(text_events) > 0, "应有文本流事件"

    # 软断言：若发起了 tool 调用，结构必须正确（API-03 控制流契约）
    tool_starts = [e for e in events if e["data"].get("type") == "tool_start"]
    for ts in tool_starts:
        assert ts["event"] == "control"
        assert ts["data"].get("tool"), "tool_start 事件必须含 tool 字段"

    # 应以 done 事件结尾
    assert events[-1]["data"].get("type") == "done"


@skip_without_db
async def test_chat_stream_invalid_session_returns_404(client):
    """对不存在的 session 发起对话应 404。"""
    bogus_id = str(uuid.uuid4())
    resp = await client.post(
        "/api/v1/chat/stream",
        json={"session_id": bogus_id, "content": "hi"},
    )
    assert resp.status_code == 404


@skip_without_db
async def test_chat_stream_rejects_empty_content(client):
    """content 为空应被 pydantic 拦截，返回 422。"""
    sess_resp = await client.post("/api/v1/sessions")
    session_id = sess_resp.json()["id"]

    resp = await client.post(
        "/api/v1/chat/stream",
        json={"session_id": session_id, "content": ""},
    )
    assert resp.status_code == 422