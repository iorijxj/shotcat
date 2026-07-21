"""跨租户越权回归测试：租户 A 的项目/章节，租户 B 必须看不到、改不了、删不掉（多租户 P4c）。

用真实的内存 SQLite（每个测试独立建库）而不是手搓 fake DB，因为 list_projects/get_chapter
这类接口会走真实的 SQL WHERE/JOIN 过滤 + session 级读过滤，手写替身很难忠实模拟。
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models.auth  # noqa: F401  # 确保 metadata 注册
import app.models.studio  # noqa: F401
from app.core.db import Base
from app.dependencies import get_current_tenant, get_current_user, get_db
from app.main import app
from app.models.auth import User
from app.models.studio import Chapter, ChapterStatus, Project, ProjectStyle, ProjectVisualStyle
from app.services.auth.tenants import TenantContext

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
USER_A = User(id="user-a", username="alice", password_hash="x", default_tenant_id=TENANT_A)
USER_B = User(id="user-b", username="bob", password_hash="x", default_tenant_id=TENANT_B)


def _new_project(project_id: str, tenant_id: str, owner_id: str | None = None) -> Project:
    return Project(
        id=project_id,
        owner_id=owner_id,
        tenant_id=tenant_id,
        name="用户 A 的项目",
        description="",
        style=ProjectStyle.real_people_city,
        visual_style=ProjectVisualStyle.live_action,
        seed=0,
        unify_style=True,
        progress=0,
        stats={},
    )


def _new_chapter(chapter_id: str, project_id: str) -> Chapter:
    return Chapter(
        id=chapter_id,
        project_id=project_id,
        index=1,
        title="第一章",
        summary="",
        raw_text="",
        condensed_text="",
        storyboard_count=0,
        status=ChapterStatus.draft,
    )


class _OwnershipTestClient:
    """每个测试独立建一个内存 SQLite，seed 完对象后用 TestClient 发请求。"""

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

    def request_as(self, user: User, tenant_id: str):
        maker = self._maker

        async def _get_db():
            async with maker() as session:
                session.info["tenant_id"] = tenant_id
                yield session

        async def _get_current_user() -> User:
            return user

        async def _get_current_tenant() -> TenantContext:
            return TenantContext(tenant_id=tenant_id, role="owner", user_id=user.id)

        app.dependency_overrides[get_db] = _get_db
        app.dependency_overrides[get_current_user] = _get_current_user
        app.dependency_overrides[get_current_tenant] = _get_current_tenant
        return TestClient(app)

    def close(self) -> None:
        app.dependency_overrides.clear()
        asyncio.run(self._engine.dispose())


def test_user_b_cannot_get_user_a_project() -> None:
    harness = _OwnershipTestClient()
    harness.seed(_new_project("proj-a", TENANT_A))
    try:
        response = harness.request_as(USER_B, TENANT_B).get("/api/v1/studio/projects/proj-a")
    finally:
        harness.close()

    assert response.status_code == 404


def test_user_a_can_get_own_project() -> None:
    harness = _OwnershipTestClient()
    harness.seed(_new_project("proj-a", TENANT_A))
    try:
        response = harness.request_as(USER_A, TENANT_A).get("/api/v1/studio/projects/proj-a")
    finally:
        harness.close()

    assert response.status_code == 200
    assert response.json()["data"]["id"] == "proj-a"


def test_user_b_cannot_update_user_a_project() -> None:
    harness = _OwnershipTestClient()
    harness.seed(_new_project("proj-a", TENANT_A))
    try:
        response = harness.request_as(USER_B, TENANT_B).patch(
            "/api/v1/studio/projects/proj-a", json={"name": "改名"}
        )
        verify = harness.request_as(USER_A, TENANT_A).get("/api/v1/studio/projects/proj-a")
    finally:
        harness.close()

    assert response.status_code == 404
    assert verify.json()["data"]["name"] == "用户 A 的项目"


def test_user_b_cannot_delete_user_a_project() -> None:
    harness = _OwnershipTestClient()
    harness.seed(_new_project("proj-a", TENANT_A))
    try:
        response = harness.request_as(USER_B, TENANT_B).delete("/api/v1/studio/projects/proj-a")
        verify = harness.request_as(USER_A, TENANT_A).get("/api/v1/studio/projects/proj-a")
    finally:
        harness.close()

    assert response.status_code == 404
    assert verify.status_code == 200


def test_user_b_project_list_does_not_include_user_a_project() -> None:
    harness = _OwnershipTestClient()
    harness.seed(
        _new_project("proj-a", TENANT_A),
        _new_project("proj-b", TENANT_B),
    )
    try:
        response = harness.request_as(USER_B, TENANT_B).get("/api/v1/studio/projects")
    finally:
        harness.close()

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]["items"]]
    assert ids == ["proj-b"]


def test_user_b_cannot_access_chapter_under_user_a_project() -> None:
    """间接挂靠场景：chapter 本身不带 owner_id，靠 project_id 反查归属。"""
    harness = _OwnershipTestClient()
    harness.seed(_new_project("proj-a", TENANT_A), _new_chapter("ch-a", "proj-a"))
    try:
        response = harness.request_as(USER_B, TENANT_B).get("/api/v1/studio/chapters/ch-a")
    finally:
        harness.close()

    assert response.status_code == 404


def test_user_a_can_access_own_chapter() -> None:
    harness = _OwnershipTestClient()
    harness.seed(_new_project("proj-a", TENANT_A), _new_chapter("ch-a", "proj-a"))
    try:
        response = harness.request_as(USER_A, TENANT_A).get("/api/v1/studio/chapters/ch-a")
    finally:
        harness.close()

    assert response.status_code == 200
    assert response.json()["data"]["id"] == "ch-a"
