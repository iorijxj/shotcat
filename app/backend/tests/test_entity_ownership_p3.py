"""四类资产归属校验回归测试（多租户 M2 P3-C）。

P3 清理掉 project_id 为空的历史公共资产后，assert_entity_owned 不再有
"project_id 为空视为公共资产放行"分支：一律走项目归属校验。

覆盖：
- 越权访问他人项目下的资产 → 404
- project_id 为空的资产 → 404（不再放行；正常数据已被 P3 清理掉）
- 访问自己项目下的资产 → 返回对象
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
USER_A = User(id="user-a", username="alice", password_hash="x")
USER_B = User(id="user-b", username="bob", password_hash="x")


async def _session() -> tuple[AsyncSession, object]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return maker(), engine


async def _seed_scene(db: AsyncSession, *, scene_id: str, project_id: str | None) -> None:
    if project_id is not None:
        db.add(User(id="user-a", username="alice", password_hash="x"))
        db.add(Project(id=project_id, name="p", style=_STYLE, owner_id="user-a"))
        await db.flush()
    db.add(Scene(id=scene_id, name="s", style=_STYLE, project_id=project_id))
    await db.flush()


@pytest.mark.asyncio
async def test_owner_can_access_own_entity() -> None:
    db, engine = await _session()
    async with db:
        await _seed_scene(db, scene_id="S1", project_id="P-A")
        obj = await assert_entity_owned(db, entity_type="scene", entity_id="S1", current_user=USER_A)
        assert obj.id == "S1"
    await engine.dispose()


@pytest.mark.asyncio
async def test_cross_user_entity_access_is_404() -> None:
    db, engine = await _session()
    async with db:
        await _seed_scene(db, scene_id="S1", project_id="P-A")
        with pytest.raises(HTTPException) as exc:
            await assert_entity_owned(db, entity_type="scene", entity_id="S1", current_user=USER_B)
        assert exc.value.status_code == 404
    await engine.dispose()


@pytest.mark.asyncio
async def test_null_project_entity_is_404_not_public() -> None:
    """P3 后不再放行 project_id 为空的资产（历史公共资产已被清理）。"""
    db, engine = await _session()
    async with db:
        await _seed_scene(db, scene_id="S1", project_id=None)
        with pytest.raises(HTTPException) as exc:
            await assert_entity_owned(db, entity_type="scene", entity_id="S1", current_user=USER_A)
        assert exc.value.status_code == 404
    await engine.dispose()
