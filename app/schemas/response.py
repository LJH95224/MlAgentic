"""统一响应格式（V1.5 PRD §7.1 全覆盖）。

PRD 要求所有 REST 接口返回结构 `{code, message, data}`：

```json
// 成功
{"code": 0, "message": "success", "data": { ... }}
// 失败
{"code": 40400, "message": "Session not found", "data": null}
```

设计要点：
- `ApiResponse[T]` 是泛型容器，业务 endpoint 用 `ApiResponse[SessionDetail]` 等具体类型
  让 OpenAPI 文档能正确推断 `data` 字段结构
- `success(data, message)` / `fail(code, message)` 是两个工厂方法，避免业务层手写魔法字符串
- 错误码集中在 `app.api.error_codes`，HTTP 状态码与业务 code 的映射由
  `app/api/exceptions.py::http_status_for_code()` 完成
- SSE 流式响应（/chat/stream）**不二次包装** —— SSE 协议本身已经是结构化事件流，
  V1.0 已有的 `event/type/...` 字段保持不变

注意：FastAPI 的请求体校验失败（422）也走 `app/api/exceptions.py::validation_handler`
统一翻译成 `ApiResponse.fail(40001, ...)`，前端只需识别 `code != 0` 即为失败。
"""

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一响应容器（PRD §7.1）。

    用法：
        @router.get("/sessions/{sid}", response_model=ApiResponse[SessionDetail])
        async def get_session(...) -> ApiResponse[SessionDetail]:
            return ApiResponse.success(detail)
    """

    code: int = Field(0, description="业务状态码：0=成功，其它见 PRD §7.2")
    message: str = Field("success", description="可读的状态描述")
    data: T | None = Field(None, description="业务数据；失败时为 null")

    @classmethod
    def success(cls, data: T | None = None, message: str = "success") -> "ApiResponse[T]":
        """成功响应工厂方法。"""
        return cls(code=0, message=message, data=data)

    @classmethod
    def fail(cls, code: int, message: str, data: T | None = None) -> "ApiResponse[T]":
        """失败响应工厂方法（业务层一般不直接调，而是 raise BusinessError）。"""
        return cls(code=code, message=message, data=data)


__all__ = ["ApiResponse"]
