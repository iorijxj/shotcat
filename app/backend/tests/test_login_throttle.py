"""登录防暴力破解回归测试（安全整改阶段三 3.1）。

走真实的 /api/v1/auth/login 路由 + 内存 SQLite 用户表，断言：
- 同一用户名连续失败达到阈值后，即使密码正确也被 429 锁定；
- 登录成功会清零该用户名的失败计数；
- 同一 IP 换用户名撞库达到 IP 阈值后同样被锁定；
- 锁定窗口过期后自动解锁。
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models.auth  # noqa: F401  # 确保 metadata 注册
from app.api.v1.routes import auth as auth_routes
from app.config import settings
from app.core.db import Base
from app.dependencies import get_db
from app.models.auth import User
from app.services.auth import login_throttle
from app.services.auth.login_throttle import reset_login_throttle
from app.services.auth.security import hash_password

USERNAME = "alice"
PASSWORD = "correct-horse"


@pytest.fixture(autouse=True)
def _clean_throttle_state():
    reset_login_throttle()
    yield
    reset_login_throttle()


@contextmanager
def login_client():
    """仅挂 auth 路由的最小应用 + 内存 SQLite（seed 一个真实密码哈希的用户）。"""
    engine = create_async_engine("sqlite+aiosqlite://")
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _seed() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with maker() as session:
            session.add(User(id="user-1", username=USERNAME, password_hash=hash_password(PASSWORD)))
            await session.commit()

    asyncio.run(_seed())

    application = FastAPI()
    application.include_router(auth_routes.router, prefix="/api/v1/auth")

    async def _get_db():
        async with maker() as session:
            yield session

    application.dependency_overrides[get_db] = _get_db
    try:
        yield TestClient(application)
    finally:
        application.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def _login(client: TestClient, username: str, password: str):
    return client.post("/api/v1/auth/login", json={"username": username, "password": password})


def test_username_locked_after_max_failures() -> None:
    with login_client() as client:
        for _ in range(settings.login_max_failures_per_user):
            assert _login(client, USERNAME, "wrong").status_code == 401
        # 锁定后即使密码正确也拒绝
        resp = _login(client, USERNAME, PASSWORD)
        assert resp.status_code == 429


def test_success_resets_username_counter() -> None:
    with login_client() as client:
        for _ in range(settings.login_max_failures_per_user - 1):
            assert _login(client, USERNAME, "wrong").status_code == 401
        assert _login(client, USERNAME, PASSWORD).status_code == 200
        # 计数已清零：再失败 threshold-1 次仍不触发锁定
        for _ in range(settings.login_max_failures_per_user - 1):
            assert _login(client, USERNAME, "wrong").status_code == 401
        assert _login(client, USERNAME, PASSWORD).status_code == 200


def test_ip_locked_after_dictionary_attack_across_usernames() -> None:
    with login_client() as client:
        # 换用户名撞库：不触发单用户名阈值，但同一来源 IP 累计达到 IP 阈值
        for i in range(settings.login_max_failures_per_ip):
            assert _login(client, f"ghost-{i}", "wrong").status_code == 401
        resp = _login(client, USERNAME, PASSWORD)
        assert resp.status_code == 429


def test_lockout_expires_after_window(monkeypatch: pytest.MonkeyPatch) -> None:
    with login_client() as client:
        for _ in range(settings.login_max_failures_per_user):
            _login(client, USERNAME, "wrong")
        assert _login(client, USERNAME, PASSWORD).status_code == 429
        # 快进到锁定窗口之后
        real_now = login_throttle._now()
        monkeypatch.setattr(
            login_throttle, "_now", lambda: real_now + settings.login_lockout_seconds + 1
        )
        assert _login(client, USERNAME, PASSWORD).status_code == 200
