"""幂等回填：把解析不到归属的历史孤儿聚合根归到系统兜底租户（多租户 M2 P4a）。

前置：先跑 ensure_tenant_schema → backfill_tenants → backfill_root_tenants。
backfill_root_tenants 已按依赖把能解析的 tenant_id 填好，但仍会留空：
  - 无主 project（owner_id 为空/指向不存在用户）
  - created_by 空串的历史 provider（及其 model）
  - 上述无主 project 派生的资产/character
本脚本把这些残留统一挂到系统兜底租户，使 8 个根实体的 tenant_id 全部非空，
为 ensure_root_tenant_not_null 转 NOT NULL 扫清残留。

只填 tenant_id 仍为空的行，可重复执行（幂等）。用法：
    uv run python -m app.cli.backfill_residual_tenants
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli.backfill_root_tenants import _sync_ensure_root_columns
from app.core.db import async_session_maker, engine
from app.models.llm import Model, Provider
from app.models.studio import Actor, Character, Costume, Project, Prop, Scene
from app.models.tenant import SYSTEM_TENANT_ID, SYSTEM_TENANT_NAME, TENANT_KIND_SYSTEM, Tenant

_ASSET_MODELS = (Scene, Prop, Costume, Actor)


@dataclass(frozen=True)
class ResidualBackfillResult:
    system_tenant_created: bool
    projects: int
    assets: int
    characters: int
    providers: int
    models: int


async def _ensure_system_tenant(db: AsyncSession) -> bool:
    """确保系统兜底租户存在；返回是否本次新建。"""
    if await db.get(Tenant, SYSTEM_TENANT_ID) is not None:
        return False
    db.add(Tenant(id=SYSTEM_TENANT_ID, name=SYSTEM_TENANT_NAME, kind=TENANT_KIND_SYSTEM))
    await db.flush()
    return True


async def backfill_residual_tenants(db: AsyncSession) -> ResidualBackfillResult:
    """把 tenant_id 仍为空的根实体归到系统兜底租户；调用方负责 commit。"""
    created = await _ensure_system_tenant(db)

    projects = list((await db.execute(select(Project).where(Project.tenant_id.is_(None)))).scalars().all())
    for project in projects:
        project.tenant_id = SYSTEM_TENANT_ID
    await db.flush()

    # 项目 tenant 补齐后，派生资产/character 优先随其项目，兜底再归系统租户。
    project_tenant = {p.id: p.tenant_id for p in (await db.execute(select(Project))).scalars().all()}

    n_assets = 0
    for model in _ASSET_MODELS:
        rows = list((await db.execute(select(model).where(model.tenant_id.is_(None)))).scalars().all())
        for row in rows:
            row.tenant_id = project_tenant.get(row.project_id) or SYSTEM_TENANT_ID
            n_assets += 1

    characters = list((await db.execute(select(Character).where(Character.tenant_id.is_(None)))).scalars().all())
    for character in characters:
        character.tenant_id = project_tenant.get(character.project_id) or SYSTEM_TENANT_ID
    await db.flush()

    providers = list((await db.execute(select(Provider).where(Provider.tenant_id.is_(None)))).scalars().all())
    for provider in providers:
        provider.tenant_id = SYSTEM_TENANT_ID
    await db.flush()

    provider_tenant = {p.id: p.tenant_id for p in (await db.execute(select(Provider))).scalars().all()}
    models = list((await db.execute(select(Model).where(Model.tenant_id.is_(None)))).scalars().all())
    for model_row in models:
        model_row.tenant_id = provider_tenant.get(model_row.provider_id) or SYSTEM_TENANT_ID

    return ResidualBackfillResult(
        system_tenant_created=created,
        projects=len(projects),
        assets=n_assets,
        characters=len(characters),
        providers=len(providers),
        models=len(models),
    )


async def _run() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_sync_ensure_root_columns)
    async with async_session_maker() as db:
        result = await backfill_residual_tenants(db)
        await db.commit()
        print(
            "残留租户回填完成："
            f"系统租户{'新建' if result.system_tenant_created else '已存在'} "
            f"projects={result.projects} assets={result.assets} characters={result.characters} "
            f"providers={result.providers} models={result.models}"
        )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
