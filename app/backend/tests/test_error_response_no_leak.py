"""全局异常处理器信息回显复核（安全整改阶段三 3.5）。

用主应用真实的 handler 函数断言：未处理异常对外只回固定的
"Internal server error"，不泄露异常原文/堆栈/内部路径；
HTTPException 的业务 detail 照常透传（这是产品行为，不是泄露）。
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from app.main import app as main_app, http_exception_handler, validation_exception_handler

SENSITIVE = r"E:\secret\internal\db.py password=hunter2"


def _build_app() -> TestClient:
    application = FastAPI()  # 与 main.py 相同：不开 debug，Starlette 不渲染 traceback
    application.add_exception_handler(RequestValidationError, validation_exception_handler)
    application.add_exception_handler(HTTPException, http_exception_handler)
    application.add_exception_handler(Exception, http_exception_handler)

    @application.get("/boom")
    async def boom():  # noqa: ANN202
        raise RuntimeError(SENSITIVE)

    @application.get("/teapot")
    async def teapot():  # noqa: ANN202
        raise HTTPException(status_code=418, detail="业务提示文案")

    return TestClient(application, raise_server_exceptions=False)


def test_unhandled_exception_returns_generic_message() -> None:
    client = _build_app()
    resp = client.get("/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["message"] == "Internal server error"
    assert "secret" not in resp.text
    assert "hunter2" not in resp.text
    assert "Traceback" not in resp.text


def test_http_exception_detail_passthrough() -> None:
    client = _build_app()
    resp = client.get("/teapot")
    assert resp.status_code == 418
    assert resp.json()["message"] == "业务提示文案"


def test_main_app_debug_flag_is_off() -> None:
    """main.py 未开 FastAPI debug：Starlette 的 ServerErrorMiddleware 不会渲染堆栈页。"""
    assert main_app.debug is False
