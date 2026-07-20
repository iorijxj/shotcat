"""登录接口限流回归测试（公众化 M1）。

用只挂中间件与哑 /auth/login 的最小应用验证：超阈值 429、可配、置 0 关闭，
且与生成类限流互不串账。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.core.rate_limit import RateLimitMiddleware, reset_generation_rate_limit


@pytest.fixture(autouse=True)
def _clean_rate_limit_state():
    reset_generation_rate_limit()
    yield
    reset_generation_rate_limit()


def _build_app() -> TestClient:
    application = FastAPI()
    application.add_middleware(RateLimitMiddleware)

    @application.post("/api/v1/auth/login")
    async def login():  # noqa: ANN202
        return {"ok": True}

    @application.post("/api/v1/film/tasks/video")
    async def create_video():  # noqa: ANN202
        return {"ok": True}

    @application.get("/api/v1/auth/login")
    async def login_get():  # noqa: ANN202
        return {"ok": True}

    return TestClient(application)


def test_login_requests_limited_per_ip() -> None:
    client = _build_app()
    limit = settings.login_rate_limit_per_minute
    for _ in range(limit):
        assert client.post("/api/v1/auth/login").status_code == 200
    resp = client.post("/api/v1/auth/login")
    assert resp.status_code == 429
    assert resp.json()["code"] == 429


def test_login_and_generation_limits_are_independent() -> None:
    """登录额度打满不应影响生成额度，反之亦然（键命名空间隔离）。"""
    client = _build_app()
    for _ in range(settings.login_rate_limit_per_minute):
        client.post("/api/v1/auth/login")
    assert client.post("/api/v1/auth/login").status_code == 429
    # 生成额度不受登录洪泛影响
    assert client.post("/api/v1/film/tasks/video").status_code == 200


def test_non_post_login_not_limited() -> None:
    client = _build_app()
    for _ in range(settings.login_rate_limit_per_minute + 5):
        assert client.get("/api/v1/auth/login").status_code == 200


def test_zero_limit_disables_login_rate_limiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "login_rate_limit_per_minute", 0)
    client = _build_app()
    for _ in range(settings.login_rate_limit_per_minute or 0 or 80):
        assert client.post("/api/v1/auth/login").status_code == 200
