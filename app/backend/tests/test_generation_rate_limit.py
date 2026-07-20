"""AI 生成类接口限流回归测试（安全整改阶段三 3.4）。

用只挂中间件与哑路由的最小应用验证：超限 429、按用户隔离、
非生成类路径（cancel/preview-prompt/GET）不受限、limit=0 关闭。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.core.rate_limit import GenerationRateLimitMiddleware, reset_generation_rate_limit
from app.services.auth.security import create_access_token


@pytest.fixture(autouse=True)
def _clean_rate_limit_state():
    reset_generation_rate_limit()
    yield
    reset_generation_rate_limit()


def _build_app() -> TestClient:
    application = FastAPI()
    application.add_middleware(GenerationRateLimitMiddleware)

    @application.post("/api/v1/film/tasks/video")
    async def create_video():  # noqa: ANN202
        return {"ok": True}

    @application.post("/api/v1/film/tasks/video/preview-prompt")
    async def preview_prompt():  # noqa: ANN202
        return {"ok": True}

    @application.post("/api/v1/studio/image-tasks/asset-batches")
    async def asset_batches():  # noqa: ANN202
        return {"ok": True}

    @application.post("/api/v1/studio/image-tasks/asset-batches/b1/cancel")
    async def cancel_batch():  # noqa: ANN202
        return {"ok": True}

    @application.post("/api/v1/script-processing/divide-async")
    async def divide():  # noqa: ANN202
        return {"ok": True}

    @application.get("/api/v1/film/tasks/video")
    async def poll_status():  # noqa: ANN202
        return {"ok": True}

    return TestClient(application)


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id=user_id)}"}


def test_generation_requests_limited_per_user() -> None:
    client = _build_app()
    limit = settings.generation_rate_limit_per_minute
    for _ in range(limit):
        assert client.post("/api/v1/film/tasks/video", headers=_auth("u1")).status_code == 200
    resp = client.post("/api/v1/film/tasks/video", headers=_auth("u1"))
    assert resp.status_code == 429
    assert resp.json()["code"] == 429


def test_limit_is_isolated_between_users() -> None:
    client = _build_app()
    for _ in range(settings.generation_rate_limit_per_minute):
        client.post("/api/v1/script-processing/divide-async", headers=_auth("u1"))
    assert client.post("/api/v1/script-processing/divide-async", headers=_auth("u1")).status_code == 429
    assert client.post("/api/v1/script-processing/divide-async", headers=_auth("u2")).status_code == 200


def test_limit_shared_across_generation_endpoints() -> None:
    """同一用户的额度是总额度，不是每端点各一份。"""
    client = _build_app()
    for _ in range(settings.generation_rate_limit_per_minute):
        client.post("/api/v1/film/tasks/video", headers=_auth("u1"))
    assert client.post("/api/v1/studio/image-tasks/asset-batches", headers=_auth("u1")).status_code == 429


def test_non_generation_paths_not_limited() -> None:
    client = _build_app()
    for _ in range(settings.generation_rate_limit_per_minute):
        client.post("/api/v1/film/tasks/video", headers=_auth("u1"))
    # 超限后：preview/cancel/GET 状态查询都不受影响
    assert client.post("/api/v1/film/tasks/video/preview-prompt", headers=_auth("u1")).status_code == 200
    assert client.post("/api/v1/studio/image-tasks/asset-batches/b1/cancel", headers=_auth("u1")).status_code == 200
    assert client.get("/api/v1/film/tasks/video", headers=_auth("u1")).status_code == 200


def test_zero_limit_disables_rate_limiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "generation_rate_limit_per_minute", 0)
    client = _build_app()
    for _ in range(30):
        assert client.post("/api/v1/film/tasks/video", headers=_auth("u1")).status_code == 200
