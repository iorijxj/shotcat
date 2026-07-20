"""幂等回填：给聚合根填 tenant_id（多租户 M2 P2）。

前置：先跑 ensure_tenant_schema（建租户表/补 users 列）+ backfill_tenants
（每个 User 有 default_tenant_id）。本脚本按依赖序回填聚合根：
  projects   ← owner_id → user.default_tenant_id
  scene/prop/costume/actor/characters ← project_id → project.tenant_id
  providers  ← created_by → user.default_tenant_id
  models     ← provider_id → provider.tenant_id
只填 tenant_id 为空的行，可重复执行。解析不到租户（无主 project、
project_id 为空的历史公共资产、created_by 空串的历史 provider）先留空，
交给 P3 清理 / P4 NOT NULL 前的独立处理。

用法：
    uv run python -m app.cli.backfill_root_tenants
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Base, async_session_maker, engine
from app.models.auth import User
from app.models.llm import Model, Provider
from app.models.studio import Actor, Character, Costume, Project, Prop, Scene

_ASSET_MODELS = (Scene, Prop, Costume, Actor)
_ROOT_TABLES = ("projects", "scenes", "props", "costumes", "actors", "characters", "providers", "models")


@dataclass(frozen=True)
class RootBackfillResult:
    projects: int
    assets: int
    characters: int
    providers: int
    models: int
    unresolved: int  # 解析不到租户、留空的聚合根数


def _sync_ensure_root_columns(sync_conn: Connection) -> None:
    """现有库补 tenant_id 列（create_all 只建新表不补列）。可重复执行。"""
    import app.models.auth  # noqa: F401
    import app.models.tenant  # noqa: F401
    import app.models.llm  # noqa: F401
    import app.models.studio  # noqa: F401
    import app.models.task  # noqa: F401
    import app.models.task_links  # noqa: F401

    Base.metadata.create_all(sync_conn)
    inspector = inspect(sync_conn)
    for table in _ROOT_TABLES:
        columns = {c["name"] for c in inspector.get_columns(table)}
        if "tenant_id" not in columns:
            sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN tenant_id VARCHAR(64) NULL"))


async def backfill_root_tenants(db: AsyncSession) -> RootBackfillResult:
    """按依赖序回填聚合根 tenant_id；只填空值，调用方负责 commit。"""
    user_tenant = {u.id: u.default_tenant_id for u in (await db.execute(select(User))).scalars().all()}
    unresolved = 0

    projects = list((await db.execute(select(Project).where(Project.tenant_id.is_(None)))).scalars().all())
    n_projects = 0
    for project in projects:
        tid = user_tenant.get(project.owner_id) if project.owner_id else None
        if tid:
            project.tenant_id = tid
            n_projects += 1
        else:
            unresolved += 1
    await db.flush()

    project_tenant = {p.id: p.tenant_id for p in (await db.execute(select(Project))).scalars().all()}

    n_assets = 0
    for model in _ASSET_MODELS:
        rows = list((await db.execute(select(model).where(model.tenant_id.is_(None)))).scalars().all())
        for row in rows:
            tid = project_tenant.get(row.project_id) if row.project_id else None
            if tid:
                row.tenant_id = tid
                n_assets += 1
            else:
                unresolved += 1

    n_characters = 0
    characters = list((await db.execute(select(Character).where(Character.tenant_id.is_(None)))).scalars().all())
    for character in characters:
        tid = project_tenant.get(character.project_id)
        if tid:
            character.tenant_id = tid
            n_characters += 1
        else:
            unresolved += 1
    await db.flush()

    n_providers = 0
    providers = list((await db.execute(select(Provider).where(Provider.tenant_id.is_(None)))).scalars().all())
    for provider in providers:
        tid = user_tenant.get(provider.created_by) if provider.created_by else None
        if tid:
            provider.tenant_id = tid
            n_providers += 1
        else:
            unresolved += 1
    await db.flush()

    provider_tenant = {p.id: p.tenant_id for p in (await db.execute(select(Provider))).scalars().all()}
    n_models = 0
    models = list((await db.execute(select(Model).where(Model.tenant_id.is_(None)))).scalars().all())
    for model_row in models:
        tid = provider_tenant.get(model_row.provider_id)
        if tid:
            model_row.tenant_id = tid
            n_models += 1
        else:
            unresolved += 1

    return RootBackfillResult(
        projects=n_projects,
        assets=n_assets,
        characters=n_characters,
        providers=n_providers,
        models=n_models,
        unresolved=unresolved,
    )


async def _run() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_sync_ensure_root_columns)
    async with async_session_maker() as db:
        result = await backfill_root_tenants(db)
        await db.commit()
        print(
            "聚合根租户回填完成："
            f"projects={result.projects} assets={result.assets} characters={result.characters} "
            f"providers={result.providers} models={result.models} 未解析(留空)={result.unresolved}"
        )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
