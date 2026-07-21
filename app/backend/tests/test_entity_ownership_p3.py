"""四类资产归属校验回归测试（多租户 M2 P4c）。

assert_entity_owned 隔离维度改为租户：当前租户取自 db.info["tenant_id"]，经聚合根
（project）的 tenant_id 比较；跨租户或不存在一律 404。

覆盖：
- 同租户访问自己资产 → 返回对象
- 跨租户访问他人资产 → 404（读过滤 + 显式比较双层）
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.models.auth import User
from app.models.studio import Project, Scene
from app.models.types import ProjectStyle
from app.services.auth.ownership import assert_entity_owned

_STYLE = ProjectStyle.real_people_city
TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
USER_A = User(id="user-a", username="alice", password_hash="x", default_tenant_id=TENANT_A)
USER_B = User(id="user-b", username="bob", password_hash="x", default_tenant_id=TENANT_B)


async def _session() -> tuple[AsyncSession, object]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return maker(), engine


async def _seed_scene(db: AsyncSession, *, scene_id: str, project_id: str, tenant_id: str) -> None:
    db.add(Project(id=project_id, name="p", style=_STYLE, owner_id=None, tenant_id=tenant_id))
    await db.flush()
    db.add(Scene(id=scene_id, name="s", style=_STYLE, project_id=project_id, tenant_id=tenant_id))
    await db.flush()


@pytest.mark.asyncio
async def test_owner_can_access_own_entity() -> None:
    db, engine = await _session()
    async with db:
        await _seed_scene(db, scene_id="S1", project_id="P-A", tenant_id=TENANT_A)
        db.info["tenant_id"] = TENANT_A
        obj = await assert_entity_owned(db, entity_type="scene", entity_id="S1", current_user=USER_A)
        assert obj.id == "S1"
    await engine.dispose()


@pytest.mark.asyncio
async def test_cross_tenant_entity_access_is_404() -> None:
    db, engine = await _session()
    async with db:
        await _seed_scene(db, scene_id="S1", project_id="P-A", tenant_id=TENANT_A)
        db.info["tenant_id"] = TENANT_B
        with pytest.raises(HTTPException) as exc:
            await assert_entity_owned(db, entity_type="scene", entity_id="S1", current_user=USER_B)
        assert exc.value.status_code == 404
    await engine.dispose()
