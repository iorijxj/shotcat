"""聚合根租户盖章回归测试（多租户 M2 P2）。

覆盖：8 个聚合根都带 tenant_id 列；before_flush 在有租户上下文时给新增根盖章、
对显式不匹配的 tenant_id 拒绝、无上下文时放行（既有行为不变）；get_current_tenant
写 session.info。读过滤 P4 才开，此处不涉及。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base, CrossTenantWriteError
from app.dependencies import get_current_tenant
from app.models.auth import User
from app.models.llm import Model, Provider
from app.models.studio import Actor, Character, Costume, Project, Prop, Scene
from app.services.auth.tenants import provision_personal_tenant

_ROOT_MODELS = [Project, Scene, Prop, Costume, Actor, Character, Provider, Model]


async def _build_session() -> tuple[AsyncSession, object]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    session_local = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return session_local(), engine


def _new_provider(pid: str, **kw) -> Provider:  # noqa: ANN003
    return Provider(id=pid, name="p", base_url="http://x", **kw)


def test_all_root_models_have_tenant_id_column() -> None:
    for model in _ROOT_MODELS:
        assert "tenant_id" in model.__table__.columns, f"{model.__name__} 缺 tenant_id 列"


@pytest.mark.asyncio
async def test_before_flush_stamps_new_root_when_context_set() -> None:
    db, engine = await _build_session()
    async with db:
        db.info["tenant_id"] = "t1"
        provider = _new_provider("p1")
        db.add(provider)
        await db.flush()
        assert provider.tenant_id == "t1"
    await engine.dispose()


@pytest.mark.asyncio
async def test_before_flush_rejects_mismatched_tenant() -> None:
    db, engine = await _build_session()
    async with db:
        db.info["tenant_id"] = "t1"
        db.add(_new_provider("p2", tenant_id="t2"))
        with pytest.raises(CrossTenantWriteError):
            await db.flush()
    await engine.dispose()


@pytest.mark.asyncio
async def test_before_flush_noop_without_context() -> None:
    db, engine = await _build_session()
    async with db:
        provider = _new_provider("p3")  # session.info 无 tenant_id
        db.add(provider)
        await db.flush()
        assert provider.tenant_id is None  # 既有行为：不盖章
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_current_tenant_stamps_session_info() -> None:
    db, engine = await _build_session()
    async with db:
        user = User(id=str(uuid.uuid4()), username="alice", password_hash="x")
        db.add(user)
        await db.flush()
        tenant = await provision_personal_tenant(db, user=user)

        ctx = await get_current_tenant(current_user=user, db=db)
        assert ctx.tenant_id == tenant.id
        assert db.info["tenant_id"] == tenant.id
    await engine.dispose()
