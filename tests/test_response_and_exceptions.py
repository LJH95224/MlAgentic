"""S1.0 统一响应基础设施单测。

覆盖：
- ApiResponse 容器的成功/失败构造
- 业务码 → HTTP 状态映射的完整性与对称性
- BusinessError + 4 个 handler 的端到端行为（用一个临时 FastAPI app 接入 handler 后实测）
- 验证 S1.0 阶段 handler **未挂到主 app**（不破坏 V1.0）
"""

from http import HTTPStatus

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api import error_codes
from app.api.exceptions import (
    HTTP_STATUS_BY_CODE,
    BusinessError,
    code_for_http_status,
    http_status_for_code,
    register_exception_handlers,
)
from app.schemas.response import ApiResponse


# ───────── ApiResponse ─────────


def test_apiresponse_success_default():
    r = ApiResponse[dict].success({"id": 1})
    assert r.code == 0
    assert r.message == "success"
    assert r.data == {"id": 1}


def test_apiresponse_success_custom_message():
    r = ApiResponse[str].success("ok", message="created")
    assert r.code == 0
    assert r.message == "created"


def test_apiresponse_fail():
    r = ApiResponse[None].fail(error_codes.NOT_FOUND, "not found")
    assert r.code == error_codes.NOT_FOUND
    assert r.message == "not found"
    assert r.data is None


def test_apiresponse_success_data_none_allowed():
    r = ApiResponse[None].success()
    assert r.code == 0
    assert r.data is None


def test_apiresponse_generic_typed_serialization():
    """泛型 data 字段 model_dump 应该输出原生 dict。"""

    class Item(BaseModel):
        name: str

    r = ApiResponse[Item].success(Item(name="a"))
    dumped = r.model_dump()
    assert dumped == {"code": 0, "message": "success", "data": {"name": "a"}}


# ───────── 业务码表 ─────────


def test_error_codes_default_messages_completeness():
    """每个非 SUCCESS 错误码必须有默认 message，否则 BusinessError 兜底失败。"""
    for name in dir(error_codes):
        if name.isupper() and isinstance(getattr(error_codes, name), int):
            code = getattr(error_codes, name)
            if code == 0 or name == "SUCCESS":
                continue
            assert code in error_codes.DEFAULT_MESSAGES, f"错误码 {name} 缺 DEFAULT_MESSAGES"


def test_http_status_mapping_covers_all_codes():
    """业务码表必须每条都在 HTTP_STATUS_BY_CODE 里有映射，避免漏配。"""
    declared = {
        getattr(error_codes, name)
        for name in dir(error_codes)
        if name.isupper() and isinstance(getattr(error_codes, name), int)
    }
    declared.discard(error_codes.SUCCESS)  # SUCCESS 不映射 HTTP 错误
    mapped = set(HTTP_STATUS_BY_CODE.keys()) - {error_codes.SUCCESS}
    missing = declared - mapped
    assert not missing, f"以下业务码缺 HTTP 映射：{missing}"


def test_http_status_for_code_known():
    """各 PRD §7.2 列出的状态码 → HTTP 都必须对应正确。"""
    assert http_status_for_code(error_codes.NOT_FOUND) == HTTPStatus.NOT_FOUND
    assert http_status_for_code(error_codes.NAME_CONFLICT) == HTTPStatus.CONFLICT
    assert http_status_for_code(error_codes.PARAM_INVALID) == HTTPStatus.BAD_REQUEST
    assert (
        http_status_for_code(error_codes.FILE_TOO_LARGE)
        == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    )
    assert (
        http_status_for_code(error_codes.UNSUPPORTED_MEDIA)
        == HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    )
    assert (
        http_status_for_code(error_codes.EMBEDDING_DIM_MISMATCH)
        == HTTPStatus.UNPROCESSABLE_ENTITY
    )
    assert (
        http_status_for_code(error_codes.CELERY_UNAVAILABLE)
        == HTTPStatus.SERVICE_UNAVAILABLE
    )


def test_http_status_for_code_unknown_defaults_to_500():
    assert http_status_for_code(99999) == HTTPStatus.INTERNAL_SERVER_ERROR


def test_code_for_http_status_known_mappings():
    """反向映射对几个典型 HTTP 状态准确。"""
    assert code_for_http_status(404) == error_codes.NOT_FOUND
    assert code_for_http_status(409) == error_codes.NAME_CONFLICT
    assert code_for_http_status(413) == error_codes.FILE_TOO_LARGE
    assert code_for_http_status(500) == error_codes.INTERNAL_ERROR


def test_code_for_http_status_unknown_4xx_defaults_to_param_invalid():
    """未列出的 4xx → PARAM_INVALID（前端能据此提示用户改请求）。"""
    assert code_for_http_status(418) == error_codes.PARAM_INVALID


def test_code_for_http_status_unknown_5xx_defaults_to_internal():
    assert code_for_http_status(599) == error_codes.INTERNAL_ERROR


# ───────── BusinessError ─────────


def test_business_error_default_message_from_table():
    """未传 message 时用 DEFAULT_MESSAGES 兜底。"""
    err = BusinessError(error_codes.NOT_FOUND)
    assert err.code == error_codes.NOT_FOUND
    assert err.message == error_codes.DEFAULT_MESSAGES[error_codes.NOT_FOUND]


def test_business_error_explicit_message_wins():
    err = BusinessError(error_codes.NOT_FOUND, "会话 xxx 不存在")
    assert err.message == "会话 xxx 不存在"


def test_business_error_str_has_code():
    err = BusinessError(error_codes.NAME_CONFLICT, "duplicate")
    assert "code=" in str(err)
    assert "duplicate" in str(err)


def test_business_error_unknown_code_falls_back():
    err = BusinessError(99999)
    assert "unknown error" in err.message


# ───────── handler 端到端 ─────────


@pytest.fixture
def handler_app():
    """临时 FastAPI app 注册全套 handler，跑端到端而不依赖主 app。"""
    app = FastAPI()
    register_exception_handlers(app)

    class _Body(BaseModel):
        name: str

    @app.get("/biz/{code}")
    def _raise_biz(code: int):
        raise BusinessError(code, f"biz-fail-{code}")

    @app.get("/http/{status_code}")
    def _raise_http(status_code: int):
        raise HTTPException(status_code=status_code, detail=f"http-{status_code}")

    @app.post("/validated")
    def _validated(body: _Body):
        return {"ok": body.name}

    return TestClient(app)


def test_handler_business_error_not_found(handler_app):
    res = handler_app.get(f"/biz/{error_codes.NOT_FOUND}")
    assert res.status_code == 404
    body = res.json()
    assert body == {
        "code": error_codes.NOT_FOUND,
        "message": f"biz-fail-{error_codes.NOT_FOUND}",
        "data": None,
    }


def test_handler_business_error_conflict(handler_app):
    res = handler_app.get(f"/biz/{error_codes.NAME_CONFLICT}")
    assert res.status_code == 409
    assert res.json()["code"] == error_codes.NAME_CONFLICT


def test_handler_business_error_immutable_field(handler_app):
    res = handler_app.get(f"/biz/{error_codes.IMMUTABLE_FIELD}")
    assert res.status_code == 400
    assert res.json()["code"] == error_codes.IMMUTABLE_FIELD


def test_handler_http_exception_404(handler_app):
    res = handler_app.get("/http/404")
    assert res.status_code == 404
    body = res.json()
    assert body["code"] == error_codes.NOT_FOUND
    assert body["message"] == "http-404"
    assert body["data"] is None


def test_handler_http_exception_unknown_4xx(handler_app):
    res = handler_app.get("/http/418")
    assert res.status_code == 418
    assert res.json()["code"] == error_codes.PARAM_INVALID


def test_handler_validation_error_returns_40001(handler_app):
    res = handler_app.post("/validated", json={})  # 缺 name
    assert res.status_code == 422
    body = res.json()
    assert body["code"] == error_codes.PARAM_INVALID
    assert body["data"] is None
    # message 应该包含字段路径，便于前端定位
    assert "name" in body["message"].lower()


def test_handler_unhandled_exception_is_translated_directly():
    """直接调 unhandled_exception_handler，确认返回 500 + INTERNAL_ERROR。

    不通过 TestClient —— Starlette 0.36+ 的 TestClient 即使注册了 Exception
    handler 也会 reraise 未捕获异常（设计如此，方便服务端栈追溯）。这里直接
    单测 handler 的契约：拿一个 RuntimeError 进来，得到统一的 ApiResponse。
    """
    import asyncio
    import json

    from app.api.exceptions import unhandled_exception_handler

    async def _run() -> None:
        response = await unhandled_exception_handler(None, RuntimeError("kaboom"))
        assert response.status_code == 500
        body = json.loads(response.body)
        assert body["code"] == error_codes.INTERNAL_ERROR
        assert body["data"] is None
        # 不向前端泄堆栈或原始错误信息
        assert "kaboom" not in body["message"]
        assert "Traceback" not in body["message"]

    asyncio.run(_run())


# ───────── handler 已挂载到主 app（S1.0b 起）─────────


def test_handlers_registered_on_main_app():
    """S1.0b 起：register_exception_handlers 已挂到主 app。

    取代 S1.0 阶段的 `not_registered_on_main_app_yet` 反向断言，
    防止后续重构不小心又把 handler 从主 app 摘掉导致返回结构回退。
    """
    from app.main import app

    handlers = getattr(app, "exception_handlers", {})
    assert BusinessError in handlers, (
        "BusinessError handler 未挂到主 app，统一响应格式失效；"
        "请在 app/main.py::create_app 里调 register_exception_handlers(app)"
    )
