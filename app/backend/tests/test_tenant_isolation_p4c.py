"""P4c：多租户硬隔离端到端回归。

用真实内存 SQLite + 真实 get_current_tenant（不 override），验证门禁→读过滤→
assert 全链路：
- 跨租户互不可见（A 看不到 B 的 project/provider）
- B2B 同租户多成员互见
- 默认安全：handler 不写任何 where，读过滤仍自动按租户隔离
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models.auth  # noqa: F401  # 确保 metadata 注册
import app.models.llm  # noqa: F401
import app.models.studio  # noqa: F401
import app.models.tenant  # noqa: F401
from app.core.db import Base
from app.dependencies import get_current_user, get_db
from app.main import app
from app.models.auth import User
from app.models.llm import Provider
from app.models.studio import Project, ProjectStyle, ProjectVisualStyle
from app.models.tenant import (
    MEMBERSHIP_ROLE_MEMBER,
    MEMBERSHIP_ROLE_OWNER,
    MEMBERSHIP_STATUS_ACTIVE,
    TENANT_KIND_ORG,
    TENANT_KIND_PERSONAL,
    Tenant,
    TenantMembership,
)


def _tenant(tid: str, kind: str = TENANT_KIND_PERSONAL) -> Tenant:
    return Tenant(id=tid, name=tid, kind=kind)


def _membership(mid: str, tid: str, uid: str, role: str = MEMBERSHIP_ROLE_OWNER) -> TenantMembership:
    return TenantMembership(id=mid, tenant_id=tid, user_id=uid, role=role, status=MEMBERSHIP_STATUS_ACTIVE)


def _user(uid: str, tid: str) -> User:
    return User(id=uid, username=uid, password_hash="x", default_tenant_id=tid)


def _project(pid: str, tid: str, name: str = "项目") -> Project:
    return Project(
        id=pid,
        tenant_id=tid,
        owner_id=None,
        name=name,
        description="",
        style=ProjectStyle.real_people_city,
        visual_style=ProjectVisualStyle.live_action,
        seed=0,
        unify_style=True,
        progress=0,
        stats={},
    )


def _provider(pid: str, tid: str, name: str = "prov") -> Provider:
    return Provider(id=pid, tenant_id=tid, name=name, base_url="http://x", created_by="")


class _Harness:
    """每个测试独立内存 SQLite，seed 完 tenant/membership/业务数据后用 TestClient 发请求。"""

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

        app.dependency_overrides[get_db] = _get_db
        app.dependency_overrides[get_current_user] = _get_current_user
        return TestClient(app)

    def read_scoped(self, tenant_id: str, stmt) -> list:
        """直接开 session、只设 tenant 上下文、执行给定 select（不加 where），验证读过滤。"""
        maker = self._maker

        async def _run() -> list:
            async with maker() as session:
                session.info["tenant_id"] = tenant_id
                return list((await session.execute(stmt)).scalars().all())

        return asyncio.run(_run())

    def close(self) -> None:
        app.dependency_overrides.clear()
        asyncio.run(self._engine.dispose())


def test_cross_tenant_project_get_is_404() -> None:
    h = _Harness()
    h.seed(
        _tenant("ta"), _tenant("tb"),
        _membership("ma", "ta", "ua"), _membership("mb", "tb", "ub"),
        _project("proj-a", "ta"),
    )
    try:
        resp = h.request_as(_user("ub", "tb")).get("/api/v1/studio/projects/proj-a")
    finally:
        h.close()
    assert resp.status_code == 404


def test_project_list_only_shows_own_tenant() -> None:
    h = _Harness()
    h.seed(
        _tenant("ta"), _tenant("tb"),
        _membership("ma", "ta", "ua"), _membership("mb", "tb", "ub"),
        _project("proj-a", "ta"), _project("proj-b", "tb"),
    )
    try:
        resp = h.request_as(_user("ub", "tb")).get("/api/v1/studio/projects")
    finally:
        h.close()
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()["data"]["items"]]
    assert ids == ["proj-b"]


def test_same_tenant_members_share_project() -> None:
    """B2B：同一 org 租户的不同成员互相可见对方创建的项目。"""
    h = _Harness()
    h.seed(
        _tenant("torg", TENANT_KIND_ORG),
        _membership("m1", "torg", "u1", MEMBERSHIP_ROLE_OWNER),
        _membership("m2", "torg", "u2", MEMBERSHIP_ROLE_MEMBER),
        _project("proj-org", "torg"),
    )
    try:
        resp = h.request_as(_user("u2", "torg")).get("/api/v1/studio/projects/proj-org")
    finally:
        h.close()
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == "proj-org"


def test_cross_tenant_provider_list_excludes_other() -> None:
    h = _Harness()
    h.seed(
        _tenant("ta"), _tenant("tb"),
        _membership("ma", "ta", "ua"), _membership("mb", "tb", "ub"),
        _provider("prov-a", "ta"), _provider("prov-b", "tb"),
    )
    try:
        resp = h.request_as(_user("ub", "tb")).get("/api/v1/llm/providers")
    finally:
        h.close()
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()["data"]["items"]]
    assert ids == ["prov-b"]


def test_read_filter_auto_scopes_select_without_where() -> None:
    """默认安全：故意 select(Project) 不写任何 where，读过滤仍按 session 租户隔离。"""
    h = _Harness()
    h.seed(_tenant("ta"), _tenant("tb"), _project("pa", "ta"), _project("pb", "tb"))
    try:
        ids = [p.id for p in h.read_scoped("ta", select(Project))]
    finally:
        h.close()
    assert ids == ["pa"]
