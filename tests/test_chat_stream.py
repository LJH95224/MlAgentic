"""流式对话接口测试（API-02 / API-03；V1.5 起 4xx 响应包统一格式）。"""

import json
import uuid

from app.api import error_codes
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

    SSE 报文（成功路径）保持 V1.0 原协议不包装，前端逻辑零改动。
    """
    # 1) 先建会话（V1.5 起响应包 data）
    sess_resp = await client.post("/api/v1/sessions")
    session_id = sess_resp.json()["data"]["id"]

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
    """对不存在的 session 发起对话应 404 + ApiResponse{code:40400, data:null}。

    V1.5 改造：BusinessError(NOT_FOUND) → 统一 JSON 响应（PRD §7.1 / §7.2）。
    """
    bogus_id = str(uuid.uuid4())
    resp = await client.post(
        "/api/v1/chat/stream",
        json={"session_id": bogus_id, "content": "hi"},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == error_codes.NOT_FOUND
    assert body["data"] is None
    assert bogus_id in body["message"]


@skip_without_db
async def test_chat_stream_rejects_empty_content(client):
    """content 为空应被 pydantic 拦截，返回 422 + ApiResponse{code:40001, data:null}。"""
    sess_resp = await client.post("/api/v1/sessions")
    session_id = sess_resp.json()["data"]["id"]

    resp = await client.post(
        "/api/v1/chat/stream",
        json={"session_id": session_id, "content": ""},
    )
    assert resp.status_code == 422
    body = resp.json()
    # V1.5 起 Pydantic 校验失败统一翻译为 PARAM_INVALID
    assert body["code"] == error_codes.PARAM_INVALID
    assert body["data"] is None
    # message 应该包含字段名以便前端定位
    assert "content" in body["message"].lower()
