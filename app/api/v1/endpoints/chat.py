"""流式对话端点（API-02 / API-03）。

POST /api/v1/chat/stream  — 基于 SSE 的流式对话

SSE 协议格式（两行式，field:value）：
    event: message
    data: {"event":"message","type":"text","content":"你"}

    event: message
    data: {"event":"message","type":"text","content":"好"}

    event: control
    data: {"event":"control","type":"tool_start","tool":"mock","args":{...}}

    event: control
    data: {"event":"control","type":"tool_end","tool":"mock","output":"..."}

    event: message
    data: {"event":"message","type":"done"}

前端使用说明：
- EventSource 的 onmessage 可接收 message 事件；用 addEventListener("control", ...) 收控制流。
- 每条 event 的 data 为 JSON，可通过 `type` 字段判断是 text | tool_start | tool_end | done。
"""

import logging

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.api.deps import DBSessionDep
from app.schemas.chat import ChatRequest
from app.services import chat_service, session_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["对话接口"])


@router.post("/stream")
async def chat_stream(
    body: ChatRequest,
    db: DBSessionDep,
) -> EventSourceResponse:
    """流式对话（SSE 协议）。

    请求体：
        - session_id: 会话 UUID（通过 POST /api/v1/sessions 创建）
        - content: 用户输入

    返回：
        - 200: SSE 事件流（media-type: text/event-stream）
        - 404: session_id 不存在
    """
    # 确认会话存在
    session = await session_service.get_session(db, body.session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"会话 {body.session_id} 不存在",
        )

    logger.info(
        "对话流开始 session=%s content=%.32s...",
        body.session_id,
        body.content,
    )

    async def event_generator():
        """异步迭代器：将 service 层产出的 SSEEvent 序列化为 SSE data 行。"""
        async for sse_event in chat_service.stream_chat(
            db, body.session_id, body.content
        ):
            yield {
                "event": sse_event.event,
                "data": sse_event.model_dump_json(by_alias=True),
            }

    return EventSourceResponse(event_generator())