"""请求级上下文变量（V1.5 KB-06）。

设计目的：把"本轮请求的 kb_ids"等"业务上下文"在不修改函数签名的情况下
传递给深层调用（如 retriever / query_knowledge_graph 工具）。

为什么不直接改工具签名传 kb_ids？
- 工具签名是暴露给 LLM 的（用 @tool 装饰），加一个 kb_ids 参数会让 LLM 误以为
  自己能选择 kb_ids，但实际上 kb_ids 必须由 endpoint 层用户请求决定
- 沿用 RAG-04 `get_current_role()` 的设计：业务上下文走 contextvar，工具内部读

并发安全：
- contextvars 跟 async 任务天然集成：每个 asyncio Task 自动继承 ContextVar 快照
- FastAPI 的请求处理器各跑各的 task，相互之间 contextvar 隔离
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

# 当前请求的 kb_ids 范围
# - None：未设置（chat 接口未传 kb_ids 字段，走 V1.0 默认行为）
# - []：明确空（不查任何 KB）
# - [...]：要查的 KB ID 列表
_current_kb_ids: ContextVar[list[uuid.UUID] | None] = ContextVar(
    "current_kb_ids", default=None
)


def set_current_kb_ids(kb_ids: list[uuid.UUID] | None) -> object:
    """设置本请求的 kb_ids 范围；返回 token 用于后续 reset。"""
    return _current_kb_ids.set(kb_ids)


def get_current_kb_ids() -> list[uuid.UUID] | None:
    """获取本请求的 kb_ids 范围；未设置返 None。"""
    return _current_kb_ids.get()


def reset_current_kb_ids(token: object) -> None:
    """退出请求时清理。"""
    _current_kb_ids.reset(token)


__all__ = [
    "set_current_kb_ids",
    "get_current_kb_ids",
    "reset_current_kb_ids",
]
