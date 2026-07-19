"""跨用户越权回归测试：用户 A 建的 LLM Provider，用户 B 必须看不到、改不了、删不掉。

用真实内存 SQLite（同 test_project_ownership.py 的思路），因为 list_providers
会走真实的 SQL WHERE 过滤逻辑。
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models.auth  # noqa: F401  # 确保 metadata 注册
import app.models.llm  # noqa: F401
from app.core.db import Base
from app.dependencies import get_current_user, get_db
from app.models.auth import User
from app.models.llm import Provider, ProviderStatus
from tests.support.llm_api_app import build_llm_only_app

USER_A = User(id="user-a", username="alice", password_hash="x")
USER_B = User(id="user-b", username="bob", password_hash="x")

llm_app = build_llm_only_app()


def _new_provider(provider_id: str, owner_id: str) -> Provider:
    return Provider(
        id=provider_id,
        name="用户 A 的供应商",
        base_url="https://api.openai.com/v1",
        api_key="sk-real-secret",
        api_secret="",
        description="",
        status=ProviderStatus.testing,
        created_by=owner_id,
    )


class _OwnershipTestClient:
    def __init__(self) -> None:
        self._engine = create_async_engine("sqlite+aiosqlite://")
        self._maker: async_sessionmaker | None = None

    def seed(self, *objs: object) -> None:
        async def _run() -> None:
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            self._maker = async_sessionmaker(self._engine, expire_on_commit=False)
            async with self._maker() as session:
                for obj in objs:
                    session.add(obj)
                await session.commit()

        asyncio.run(_run())

    def request_as(self, user: User) -> TestClient:
        maker = self._maker

        async def _get_db():
            async with maker() as session:
                yield session

        async def _get_current_user() -> User:
            return user

        llm_app.dependency_overrides[get_db] = _get_db
        llm_app.dependency_overrides[get_current_user] = _get_current_user
        return TestClient(llm_app)

    def close(self) -> None:
        llm_app.dependency_overrides.clear()
        asyncio.run(self._engine.dispose())


def test_user_b_cannot_get_user_a_provider() -> None:
    harness = _OwnershipTestClient()
    harness.seed(_new_provider("prov-a", USER_A.id))
    try:
        response = harness.request_as(USER_B).get("/api/v1/llm/providers/prov-a")
    finally:
        harness.close()

    assert response.status_code == 404


def test_user_a_can_get_own_provider() -> None:
    harness = _OwnershipTestClient()
    harness.seed(_new_provider("prov-a", USER_A.id))
    try:
        response = harness.request_as(USER_A).get("/api/v1/llm/providers/prov-a")
    finally:
        harness.close()

    assert response.status_code == 200
    assert response.json()["data"]["id"] == "prov-a"


def test_user_b_cannot_change_user_a_provider_base_url(monkeypatch) -> None:
    """回归验证阶段二核心攻击链已堵住：改不了别人 provider 的 base_url。"""
    harness = _OwnershipTestClient()
    harness.seed(_new_provider("prov-a", USER_A.id))
    try:
        response = harness.request_as(USER_B).patch(
            "/api/v1/llm/providers/prov-a", json={"base_url": "https://attacker.example.com"}
        )
        verify = harness.request_as(USER_A).get("/api/v1/llm/providers/prov-a")
    finally:
        harness.close()

    assert response.status_code == 404
    assert verify.json()["data"]["base_url"] == "https://api.openai.com/v1"


def test_user_b_cannot_delete_user_a_provider() -> None:
    harness = _OwnershipTestClient()
    harness.seed(_new_provider("prov-a", USER_A.id))
    try:
        response = harness.request_as(USER_B).delete("/api/v1/llm/providers/prov-a")
        verify = harness.request_as(USER_A).get("/api/v1/llm/providers/prov-a")
    finally:
        harness.close()

    assert response.status_code == 404
    assert verify.status_code == 200


def test_user_b_provider_list_does_not_include_user_a_provider() -> None:
    harness = _OwnershipTestClient()
    harness.seed(
        _new_provider("prov-a", USER_A.id),
        _new_provider("prov-b", USER_B.id),
    )
    try:
        response = harness.request_as(USER_B).get("/api/v1/llm/providers")
    finally:
        harness.close()

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]["items"]]
    assert ids == ["prov-b"]


def test_legacy_provider_with_empty_created_by_is_public() -> None:
    """迁移期兼容：存量数据 created_by 为空时，任何登录用户都能访问（不是安全洞，是过渡语义）。"""
    harness = _OwnershipTestClient()
    harness.seed(_new_provider("prov-legacy", ""))
    try:
        response = harness.request_as(USER_B).get("/api/v1/llm/providers/prov-legacy")
    finally:
        harness.close()

    assert response.status_code == 200
