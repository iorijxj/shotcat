"""跨租户越权回归测试：租户 A 的 LLM Provider，租户 B 必须看不到、改不了、删不掉（P4c）。

用真实内存 SQLite（同 test_project_ownership.py 的思路），因为 list_providers 会走真实
的 SQL WHERE 过滤。llm_only_app 无全局门禁，故在 _get_db 里直接盖 session.info 的租户，
供路由的 get_current_tenant 依赖与 assert_provider_owned 使用。
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models.auth  # noqa: F401  # 确保 metadata 注册
import app.models.llm  # noqa: F401
from app.core.db import Base
from app.dependencies import get_current_tenant, get_current_user, get_db
from app.models.auth import User
from app.models.llm import Provider, ProviderStatus
from app.services.auth.tenants import TenantContext
from tests.support.llm_api_app import build_llm_only_app

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
USER_A = User(id="user-a", username="alice", password_hash="x", default_tenant_id=TENANT_A)
USER_B = User(id="user-b", username="bob", password_hash="x", default_tenant_id=TENANT_B)

llm_app = build_llm_only_app()


def _new_provider(provider_id: str, tenant_id: str, owner_id: str = "") -> Provider:
    return Provider(
        id=provider_id,
        name="用户 A 的供应商",
        base_url="https://api.openai.com/v1",
        api_key="sk-real-secret",
        api_secret="",
        description="",
        status=ProviderStatus.testing,
        created_by=owner_id,
        tenant_id=tenant_id,
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

    def request_as(self, user: User, tenant_id: str) -> TestClient:
        maker = self._maker

        async def _get_db():
            async with maker() as session:
                session.info["tenant_id"] = tenant_id
                yield session

        async def _get_current_user() -> User:
            return user

        async def _get_current_tenant() -> TenantContext:
            return TenantContext(tenant_id=tenant_id, role="owner", user_id=user.id)

        llm_app.dependency_overrides[get_db] = _get_db
        llm_app.dependency_overrides[get_current_user] = _get_current_user
        llm_app.dependency_overrides[get_current_tenant] = _get_current_tenant
        return TestClient(llm_app)

    def close(self) -> None:
        llm_app.dependency_overrides.clear()
        asyncio.run(self._engine.dispose())


def test_user_b_cannot_get_user_a_provider() -> None:
    harness = _OwnershipTestClient()
    harness.seed(_new_provider("prov-a", TENANT_A))
    try:
        response = harness.request_as(USER_B, TENANT_B).get("/api/v1/llm/providers/prov-a")
    finally:
        harness.close()

    assert response.status_code == 404


def test_user_a_can_get_own_provider() -> None:
    harness = _OwnershipTestClient()
    harness.seed(_new_provider("prov-a", TENANT_A))
    try:
        response = harness.request_as(USER_A, TENANT_A).get("/api/v1/llm/providers/prov-a")
    finally:
        harness.close()

    assert response.status_code == 200
    assert response.json()["data"]["id"] == "prov-a"


def test_user_b_cannot_change_user_a_provider_base_url(monkeypatch) -> None:
    """回归验证阶段二核心攻击链已堵住：改不了别人 provider 的 base_url。"""
    harness = _OwnershipTestClient()
    harness.seed(_new_provider("prov-a", TENANT_A))
    try:
        response = harness.request_as(USER_B, TENANT_B).patch(
            "/api/v1/llm/providers/prov-a", json={"base_url": "https://attacker.example.com"}
        )
        verify = harness.request_as(USER_A, TENANT_A).get("/api/v1/llm/providers/prov-a")
    finally:
        harness.close()

    assert response.status_code == 404
    assert verify.json()["data"]["base_url"] == "https://api.openai.com/v1"


def test_user_b_cannot_delete_user_a_provider() -> None:
    harness = _OwnershipTestClient()
    harness.seed(_new_provider("prov-a", TENANT_A))
    try:
        response = harness.request_as(USER_B, TENANT_B).delete("/api/v1/llm/providers/prov-a")
        verify = harness.request_as(USER_A, TENANT_A).get("/api/v1/llm/providers/prov-a")
    finally:
        harness.close()

    assert response.status_code == 404
    assert verify.status_code == 200


def test_user_b_provider_list_does_not_include_user_a_provider() -> None:
    harness = _OwnershipTestClient()
    harness.seed(
        _new_provider("prov-a", TENANT_A),
        _new_provider("prov-b", TENANT_B),
    )
    try:
        response = harness.request_as(USER_B, TENANT_B).get("/api/v1/llm/providers")
    finally:
        harness.close()

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]["items"]]
    assert ids == ["prov-b"]
