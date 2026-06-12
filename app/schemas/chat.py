"""对话 & SSE 流相关的 Pydantic Schema。"""

import uuid
from typing import Literal

from pydantic import BaseModel, Field


# ──────────────── 请求模型 ────────────────


class ChatRequest(BaseModel):
    """POST /api/v1/chat/stream 请求体（API-02 + V1.5 KB-06）。"""

    session_id: uuid.UUID = Field(..., description="会话 ID")
    content: str = Field(..., min_length=1, description="用户输入的消息文本")
    # V1.5 KB-06：可选传入要检索的知识库 ID 列表
    # - None / 字段不传：使用 V1.0 默认行为（查 `knowledge_chunks` 单 Collection，向后兼容）
    # - 空列表 []：明确表示"不查任何 KB"，retriever 直接返"无结果"
    # - 非空：在指定的几个 KB 上跨 Collection 搜索 + 合并重排序
    kb_ids: list[uuid.UUID] | None = Field(
        None,
        description="可选，指定本轮对话使用的知识库列表；不传则使用 V1.0 默认行为",
    )


# ──────────────── SSE 事件模型 ────────────────

# 前端通过 text/event-stream 接收，每条 event 的 data 是以下模型之一的 JSON。
# 区分方式：type 字段。
#
# 示例前端解析（JavaScript）：
#   const source = new EventSource(...);
#   source.addEventListener("message", (e) => {
#     const payload = JSON.parse(e.data);
#     if (payload.type === "text") { /* 追加到聊天窗口 */ }
#     if (payload.type === "control" && payload.control_type === "tool_start") { /* 显示加载态 */ }
#   });


class TextEvent(BaseModel):
    """文本流事件 —— 打字机效果的逐 token 文本。"""

    event: Literal["message"] = "message"
    type: Literal["text"] = "text"
    content: str = Field(..., description="文本块（一个 token 或一组 token）")


class ToolStartEvent(BaseModel):
    """控制流事件 —— 工具开始执行。"""

    event: Literal["control"] = "control"
    type: Literal["tool_start"] = "tool_start"
    tool: str = Field(..., description="工具名称")
    # args 类型用 Any 而非 dict[str,str]：工具入参可能含 list / dict / int 等
    # 嵌套类型；V1.5 KB-06 还要塞 _kb_ids: list[str]
    args: dict | None = Field(None, description="工具入参（任意 JSON 兼容字段）")


class ToolEndEvent(BaseModel):
    """控制流事件 —— 工具执行结束。"""

    event: Literal["control"] = "control"
    type: Literal["tool_end"] = "tool_end"
    tool: str = Field(..., description="工具名称")
    output: str | None = Field(None, description="工具输出摘要")


class DoneEvent(BaseModel):
    """流结束标志。"""

    event: Literal["message"] = "message"
    type: Literal["done"] = "done"


# 联合类型：所有可能下行的 SSE 事件
SSEEvent = TextEvent | ToolStartEvent | ToolEndEvent | DoneEvent