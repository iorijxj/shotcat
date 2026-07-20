"""租户存量回填回归测试（多租户 M2 P1）。

覆盖：无租户用户被建 1 人租户、重复执行幂等、有 membership 但缺默认指针被补齐。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.cli.backfill_tenants import backfill_tenants
from app.core.db import Base
from app.models.auth import User
from app.models.tenant import TenantMembership


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
async def test_backfill_provisions_tenant_for_each_user() -> None:
    db, engine = await _build_session()
    async with db:
        u1 = await _add_user(db, "u1")
        u2 = await _add_user(db, "u2")

        result = await backfill_tenants(db)
        await db.commit()

        assert result.created == 2
        assert result.fixed == 0
        for user in (u1, u2):
            await db.refresh(user)
            assert user.default_tenant_id is not None
        total_memberships = (await db.execute(select(func.count()).select_from(TenantMembership))).scalar_one()
        assert total_memberships == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_backfill_is_idempotent() -> None:
    db, engine = await _build_session()
    async with db:
        await _add_user(db, "solo")
        first = await backfill_tenants(db)
        await db.commit()
        assert first.created == 1

        second = await backfill_tenants(db)
        await db.commit()
        assert second.created == 0
        assert second.skipped == 1

        total_memberships = (await db.execute(select(func.count()).select_from(TenantMembership))).scalar_one()
        assert total_memberships == 1  # 没有重复建租户
    await engine.dispose()


@pytest.mark.asyncio
async def test_backfill_repairs_missing_default_pointer() -> None:
    db, engine = await _build_session()
    async with db:
        user = await _add_user(db, "dangling")
        await backfill_tenants(db)  # 先建好租户与 membership
        user.default_tenant_id = None  # 模拟历史/异常：有 membership 但指针为空
        await db.flush()

        result = await backfill_tenants(db)
        await db.commit()

        assert result.created == 0
        assert result.fixed == 1
        await db.refresh(user)
        assert user.default_tenant_id is not None
    await engine.dispose()
