"""统一异常处理（V1.5 PRD §7.1 / §7.2）。

把不同来源的失败统一翻译成 `ApiResponse.fail(code, message)` + 正确的 HTTP 状态码：

| 触发源 | 翻译规则 |
|---|---|
| 业务层 `raise BusinessError(code, message)` | `code` 映射 HTTP；body = ApiResponse(code, message, None) |
| FastAPI `HTTPException(status_code=4xx, detail=...)` | status_code → 业务 code；detail → message |
| Pydantic `RequestValidationError`（422 自动校验失败） | 业务 code = PARAM_INVALID (40001)；HTTP 仍 422；message = 字段错误首条 |
| 未捕获 `Exception` | 500 + INTERNAL_ERROR；message = 通用文案；详情写日志 |

设计要点：
- `register_exception_handlers(app)` 是显式注册函数，**S1.0b 才挂到 app**，
  S1.0 当前只暴露符号，避免破坏 V1.0 已有的"直接返回 Pydantic"路径
- 业务层永远 `raise BusinessError(...)`，绝不手写 `JSONResponse({"code":...})`
- 所有 response body 都用 `ApiResponse.fail()` 构造，结构由 Pydantic 保证
"""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import error_codes
from app.schemas.response import ApiResponse

logger = logging.getLogger(__name__)


# ───────── 业务异常 ─────────


class BusinessError(Exception):
    """业务层抛出的异常，由统一 handler 翻译为 ApiResponse。

    用法：
        raise BusinessError(error_codes.NOT_FOUND, f"会话 {sid} 不存在")
        raise BusinessError(error_codes.NAME_CONFLICT)  # 用 DEFAULT_MESSAGES 兜底
    """

    def __init__(
        self,
        code: int,
        message: str | None = None,
        data: Any = None,
    ) -> None:
        self.code = code
        self.message = message or error_codes.DEFAULT_MESSAGES.get(code, "unknown error")
        self.data = data
        super().__init__(f"[code={code}] {self.message}")


# ───────── 业务 code → HTTP status 映射 ─────────


# 单一真相源：业务 code 决定 HTTP 状态码
HTTP_STATUS_BY_CODE: dict[int, int] = {
    error_codes.SUCCESS: HTTPStatus.OK,
    error_codes.PARAM_INVALID: HTTPStatus.BAD_REQUEST,            # 400
    error_codes.IMMUTABLE_FIELD: HTTPStatus.BAD_REQUEST,          # 400
    error_codes.NOT_FOUND: HTTPStatus.NOT_FOUND,                  # 404
    error_codes.NAME_CONFLICT: HTTPStatus.CONFLICT,               # 409
    error_codes.FILE_TOO_LARGE: HTTPStatus.REQUEST_ENTITY_TOO_LARGE,  # 413
    error_codes.UNSUPPORTED_MEDIA: HTTPStatus.UNSUPPORTED_MEDIA_TYPE,  # 415
    error_codes.EMBEDDING_DIM_MISMATCH: HTTPStatus.UNPROCESSABLE_ENTITY,  # 422
    error_codes.INTERNAL_ERROR: HTTPStatus.INTERNAL_SERVER_ERROR,  # 500
    error_codes.CELERY_UNAVAILABLE: HTTPStatus.SERVICE_UNAVAILABLE,  # 503
}


def http_status_for_code(code: int) -> int:
    """业务 code → HTTP status；未知 code 默认 500。"""
    return HTTP_STATUS_BY_CODE.get(code, HTTPStatus.INTERNAL_SERVER_ERROR)


# HTTP 4xx/5xx → 业务 code 的反向映射（用于 HTTPException 翻译）
# 只在 V1.0 的老代码或第三方库直接 raise HTTPException 时生效
_CODE_BY_HTTP_STATUS: dict[int, int] = {
    HTTPStatus.BAD_REQUEST: error_codes.PARAM_INVALID,
    HTTPStatus.NOT_FOUND: error_codes.NOT_FOUND,
    HTTPStatus.CONFLICT: error_codes.NAME_CONFLICT,
    HTTPStatus.REQUEST_ENTITY_TOO_LARGE: error_codes.FILE_TOO_LARGE,
    HTTPStatus.UNSUPPORTED_MEDIA_TYPE: error_codes.UNSUPPORTED_MEDIA,
    HTTPStatus.UNPROCESSABLE_ENTITY: error_codes.EMBEDDING_DIM_MISMATCH,
    HTTPStatus.INTERNAL_SERVER_ERROR: error_codes.INTERNAL_ERROR,
    HTTPStatus.SERVICE_UNAVAILABLE: error_codes.CELERY_UNAVAILABLE,
}


def code_for_http_status(http_status: int) -> int:
    """HTTP status → 业务 code；未映射的 4xx 走 PARAM_INVALID，5xx 走 INTERNAL_ERROR。"""
    if http_status in _CODE_BY_HTTP_STATUS:
        return _CODE_BY_HTTP_STATUS[http_status]
    if 400 <= http_status < 500:
        return error_codes.PARAM_INVALID
    return error_codes.INTERNAL_ERROR


# ───────── handler ─────────


def _build_json_response(http_status: int, code: int, message: str) -> JSONResponse:
    """构造统一的 JSONResponse；body 通过 ApiResponse.fail 序列化。"""
    body = ApiResponse[None].fail(code=code, message=message).model_dump()
    return JSONResponse(status_code=http_status, content=jsonable_encoder(body))


async def business_error_handler(_: Request, exc: BusinessError) -> JSONResponse:
    """业务层 BusinessError → ApiResponse + 对应 HTTP 状态。"""
    http_status = http_status_for_code(exc.code)
    # 5xx 失败要记 error 日志，4xx 信息级日志（避免噪音）
    if http_status >= 500:
        logger.error("业务异常 code=%s message=%s", exc.code, exc.message, exc_info=exc)
    else:
        logger.info("业务返回失败 code=%s message=%s", exc.code, exc.message)
    return _build_json_response(http_status, exc.code, exc.message)


async def http_exception_handler(
    _: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """老代码 / 第三方库直接 raise HTTPException → 翻译为 ApiResponse。

    保留原 status_code（前端 / 浏览器 / 中间件可能依赖），body 改成 ApiResponse 包装。
    """
    code = code_for_http_status(exc.status_code)
    message = str(exc.detail) if exc.detail else error_codes.DEFAULT_MESSAGES.get(
        code, "request failed"
    )
    return _build_json_response(exc.status_code, code, message)


async def validation_exception_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    """Pydantic 请求体校验失败 → PARAM_INVALID (40001) + HTTP 422。

    message 取首条字段错误，方便前端直接展示。完整错误清单写日志。
    """
    errors = exc.errors()
    first = errors[0] if errors else {}
    field_path = ".".join(str(x) for x in first.get("loc", []) if x != "body")
    raw_msg = first.get("msg", "参数校验失败")
    message = f"{field_path}: {raw_msg}" if field_path else raw_msg

    logger.info(
        "请求参数校验失败 path=%s errors=%s", getattr(_, "url", "?"), errors
    )

    return _build_json_response(
        status.HTTP_422_UNPROCESSABLE_ENTITY, error_codes.PARAM_INVALID, message
    )


async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """兜底：未捕获的异常一律 500 + INTERNAL_ERROR；具体堆栈写日志，不返回给前端。"""
    logger.exception("未捕获异常 exc=%r", exc)
    return _build_json_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_codes.INTERNAL_ERROR,
        error_codes.DEFAULT_MESSAGES[error_codes.INTERNAL_ERROR],
    )


# ───────── 注册入口（S1.0b 才挂到 app） ─────────


def register_exception_handlers(app: FastAPI) -> None:
    """把全套 handler 注册到 FastAPI app。

    本函数在 S1.0 阶段**不被调用**，仅暴露符号供 S1.0b 与单测使用，
    避免在统一响应改造完成前破坏 V1.0 已有 endpoint 的返回结构。
    """
    app.add_exception_handler(BusinessError, business_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)


__all__ = [
    "BusinessError",
    "register_exception_handlers",
    "business_error_handler",
    "http_exception_handler",
    "validation_exception_handler",
    "unhandled_exception_handler",
    "http_status_for_code",
    "code_for_http_status",
    "HTTP_STATUS_BY_CODE",
]
