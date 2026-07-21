"""聚合根租户回填回归测试（多租户 M2 P2）。

覆盖：按依赖序把 tenant_id 从 user→project→资产/character、user→provider→model
传播；解析不到租户（无主 project / project_id 为空的资产 / created_by 空串的
provider）留空并计入 unresolved；重复执行不新增写入；旧库补列。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.cli.backfill_root_tenants import (
    _sync_ensure_root_columns,
    backfill_root_tenants,
)
from app.core.db import Base
from app.models.auth import User
from tests.conftest import assets_project_id_nullable, root_tenant_id_nullable
from app.models.llm import Model, ModelCategoryKey, Provider
from app.models.studio import Character, Project, Scene
from app.models.types import ProjectStyle


async def _build_session() -> tuple[AsyncSession, object]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    session_local = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    with assets_project_id_nullable(), root_tenant_id_nullable():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    return session_local(), engine


async def _seed(db: AsyncSession) -> None:
    db.add(User(id="U", username="u", password_hash="x", default_tenant_id="T"))
    db.add(Project(id="P1", name="p1", style=ProjectStyle.real_people_city, owner_id="U"))
    db.add(Project(id="P2", name="p2", style=ProjectStyle.real_people_city, owner_id=None))  # 无主
    db.add(Scene(id="S1", name="s1", style=ProjectStyle.real_people_city, project_id="P1"))
    db.add(Scene(id="S2", name="s2", style=ProjectStyle.real_people_city, project_id=None))  # 公共
    db.add(Character(id="C1", project_id="P1", name="c1", style=ProjectStyle.real_people_city))
    db.add(Provider(id="PV", name="pv", base_url="http://x", created_by="U"))
    db.add(Provider(id="PV2", name="pv2", base_url="http://x", created_by=""))  # 历史空 created_by
    db.add(Model(id="M", name="m", category=ModelCategoryKey.text, provider_id="PV"))
    await db.flush()


@pytest.mark.asyncio
async def test_backfill_propagates_tenant_by_dependency_order() -> None:
    db, engine = await _build_session()
    async with db:
        await _seed(db)
        result = await backfill_root_tenants(db)
        await db.commit()

        assert (await db.get(Project, "P1")).tenant_id == "T"
        assert (await db.get(Project, "P2")).tenant_id is None
        assert (await db.get(Scene, "S1")).tenant_id == "T"
        assert (await db.get(Scene, "S2")).tenant_id is None
        assert (await db.get(Character, "C1")).tenant_id == "T"
        assert (await db.get(Provider, "PV")).tenant_id == "T"
        assert (await db.get(Provider, "PV2")).tenant_id is None
        assert (await db.get(Model, "M")).tenant_id == "T"

        assert (result.projects, result.assets, result.characters, result.providers, result.models) == (1, 1, 1, 1, 1)
        assert result.unresolved == 3  # P2 + S2 + PV2
    await engine.dispose()


@pytest.mark.asyncio
async def test_backfill_is_idempotent() -> None:
    db, engine = await _build_session()
    async with db:
        await _seed(db)
        await backfill_root_tenants(db)
        await db.commit()

        second = await backfill_root_tenants(db)
        await db.commit()
        assert (second.projects, second.assets, second.characters, second.providers, second.models) == (0, 0, 0, 0, 0)
    await engine.dispose()


def test_ensure_adds_missing_tenant_id_column() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        # 旧 providers 表无 tenant_id
        conn.execute(
            text("CREATE TABLE providers (id VARCHAR(64) PRIMARY KEY, name VARCHAR(255), base_url VARCHAR(1024))")
        )
    with engine.begin() as conn:
        _sync_ensure_root_columns(conn)
        columns = {c["name"] for c in inspect(conn).get_columns("providers")}
        assert "tenant_id" in columns
    with engine.begin() as conn:  # 幂等
        _sync_ensure_root_columns(conn)
    engine.dispose()
