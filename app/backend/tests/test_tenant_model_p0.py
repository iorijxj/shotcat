"""租户模型与解析回归测试（多租户 M2 P0）。

覆盖：建号即建 tenant-of-one（owner membership + 回写 default_tenant_id）、
(tenant_id, user_id) 唯一约束、resolve_active_tenant 的默认/回落/无租户分支。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.models.auth import User
from app.models.tenant import (
    MEMBERSHIP_ROLE_OWNER,
    MEMBERSHIP_STATUS_ACTIVE,
    TENANT_KIND_PERSONAL,
    Tenant,
    TenantMembership,
)
from app.services.auth.tenants import (
    TenantContext,
    provision_personal_tenant,
    resolve_active_tenant,
)


async def _build_session() -> tuple[AsyncSession, object]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    session_local = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return session_local(), engine


async def _add_user(db: AsyncSession, username: str) -> User:
    user = User(id=str(uuid.uuid4()), username=username, password_hash="x")
    db.add(user)
    await db.flush()
    return user


@pytest.mark.asyncio
async def test_provision_personal_tenant_creates_owner_membership() -> None:
    db, engine = await _build_session()
    async with db:
        user = await _add_user(db, "alice")
        tenant = await provision_personal_tenant(db, user=user)
        await db.commit()

        assert tenant.kind == TENANT_KIND_PERSONAL
        assert user.default_tenant_id == tenant.id

        membership = (await db.execute(TenantMembership.__table__.select())).mappings().all()
        assert len(membership) == 1
        assert membership[0]["tenant_id"] == tenant.id
        assert membership[0]["user_id"] == user.id
        assert membership[0]["role"] == MEMBERSHIP_ROLE_OWNER
        assert membership[0]["status"] == MEMBERSHIP_STATUS_ACTIVE
    await engine.dispose()


@pytest.mark.asyncio
async def test_membership_unique_per_tenant_user() -> None:
    db, engine = await _build_session()
    async with db:
        user = await _add_user(db, "bob")
        tenant = await provision_personal_tenant(db, user=user)
        db.add(
            TenantMembership(
                id=str(uuid.uuid4()),
                tenant_id=tenant.id,
                user_id=user.id,
                role=MEMBERSHIP_ROLE_OWNER,
                status=MEMBERSHIP_STATUS_ACTIVE,
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()
    await engine.dispose()


@pytest.mark.asyncio
async def test_resolve_active_tenant_uses_default() -> None:
    db, engine = await _build_session()
    async with db:
        user = await _add_user(db, "carol")
        tenant = await provision_personal_tenant(db, user=user)
        await db.commit()

        ctx = await resolve_active_tenant(db, user)
        assert isinstance(ctx, TenantContext)
        assert ctx.tenant_id == tenant.id
        assert ctx.role == MEMBERSHIP_ROLE_OWNER
        assert ctx.user_id == user.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_resolve_active_tenant_falls_back_to_single_membership() -> None:
    db, engine = await _build_session()
    async with db:
        user = await _add_user(db, "dave")
        tenant = await provision_personal_tenant(db, user=user)
        # 清掉便捷指针，只剩唯一 membership 时应回落解析到它。
        user.default_tenant_id = None
        await db.flush()

        ctx = await resolve_active_tenant(db, user)
        assert ctx.tenant_id == tenant.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_resolve_active_tenant_without_membership_raises_403() -> None:
    db, engine = await _build_session()
    async with db:
        user = await _add_user(db, "erin")  # 未建租户
        with pytest.raises(HTTPException) as exc_info:
            await resolve_active_tenant(db, user)
        assert exc_info.value.status_code == 403
    await engine.dispose()
