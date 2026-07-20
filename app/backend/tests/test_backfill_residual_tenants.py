"""残留租户回填回归测试（多租户 M2 P4a）。

覆盖：把 tenant_id 仍为空的孤儿根实体（无主 project、created_by 空串 provider、
其派生资产/character/model）归到系统兜底租户；已解析的行不动；系统租户幂等新建；
重复执行不再改动。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.cli.backfill_residual_tenants import backfill_residual_tenants
from app.core.db import Base
from app.models.llm import Model, ModelCategoryKey, Provider
from app.models.studio import Actor, Character, Costume, Project, Prop, Scene
from app.models.tenant import SYSTEM_TENANT_ID, Tenant
from app.models.types import ProjectStyle

_STYLE = ProjectStyle.real_people_city


async def _build_session() -> tuple[AsyncSession, object]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return maker(), engine


async def _seed(db: AsyncSession) -> None:
    # 已解析归属（T1）——不应被改动
    db.add(Tenant(id="T1", name="租户1", kind="personal"))
    db.add(Project(id="P_ok", name="ok", style=_STYLE, owner_id="U", tenant_id="T1"))
    db.add(Scene(id="S_ok", name="s_ok", style=_STYLE, project_id="P_ok", tenant_id="T1"))
    db.add(Provider(id="PV_ok", name="pv_ok", base_url="http://x", created_by="U", tenant_id="T1"))
    # 孤儿（tenant_id 空）——应归系统租户
    db.add(Project(id="P_orphan", name="orphan", style=_STYLE, owner_id=None, tenant_id=None))
    db.add(Scene(id="S_orphan", name="s_orphan", style=_STYLE, project_id="P_orphan", tenant_id=None))
    db.add(Prop(id="Pr_orphan", name="pr_orphan", style=_STYLE, project_id="P_orphan", tenant_id=None))
    db.add(Costume(id="Co_orphan", name="co_orphan", style=_STYLE, project_id="P_orphan", tenant_id=None))
    db.add(Actor(id="A_orphan", name="a_orphan", style=_STYLE, project_id="P_orphan", tenant_id=None))
    db.add(Character(id="C_orphan", project_id="P_orphan", name="c_orphan", style=_STYLE, tenant_id=None))
    db.add(Provider(id="PV_orphan", name="pv_orphan", base_url="http://x", created_by="", tenant_id=None))
    db.add(Model(id="M_orphan", name="m", category=ModelCategoryKey.text, provider_id="PV_orphan", tenant_id=None))
    await db.flush()


@pytest.mark.asyncio
async def test_residual_backfill_assigns_orphans_to_system_tenant() -> None:
    db, engine = await _build_session()
    async with db:
        await _seed(db)
        result = await backfill_residual_tenants(db)
        await db.commit()

        assert result.system_tenant_created is True
        assert await db.get(Tenant, SYSTEM_TENANT_ID) is not None

        # 孤儿全部归系统租户
        for model, oid in [(Project, "P_orphan"), (Scene, "S_orphan"), (Prop, "Pr_orphan"),
                           (Costume, "Co_orphan"), (Actor, "A_orphan"), (Character, "C_orphan"),
                           (Provider, "PV_orphan"), (Model, "M_orphan")]:
            assert (await db.get(model, oid)).tenant_id == SYSTEM_TENANT_ID

        # 已解析的行不动
        assert (await db.get(Project, "P_ok")).tenant_id == "T1"
        assert (await db.get(Scene, "S_ok")).tenant_id == "T1"
        assert (await db.get(Provider, "PV_ok")).tenant_id == "T1"

        assert (result.projects, result.assets, result.characters, result.providers, result.models) == (1, 4, 1, 1, 1)
    await engine.dispose()


@pytest.mark.asyncio
async def test_residual_backfill_is_idempotent() -> None:
    db, engine = await _build_session()
    async with db:
        await _seed(db)
        await backfill_residual_tenants(db)
        await db.commit()

        second = await backfill_residual_tenants(db)
        await db.commit()
        assert second.system_tenant_created is False
        assert (second.projects, second.assets, second.characters, second.providers, second.models) == (0, 0, 0, 0, 0)
    await engine.dispose()
