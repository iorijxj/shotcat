"""幂等回填：给每个存量 User 建 personal 租户 + owner membership + default_tenant_id
（多租户 M2 P1）。

前置：先跑 app.cli.ensure_tenant_schema 建租户表并给 users 补列。
幂等：已有 membership 的用户跳过；有 membership 但 default_tenant_id 为空的用户
只补指针（历史/异常数据修复）。可重复执行。

用法：
    uv run python -m app.cli.backfill_tenants
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_maker
from app.models.auth import User
from app.models.tenant import TenantMembership
from app.services.auth.tenants import provision_personal_tenant


@dataclass(frozen=True)
class BackfillResult:
    """回填统计：新建租户 / 只补默认指针 / 原样跳过。"""

    created: int
    fixed: int
    skipped: int


async def backfill_tenants(db: AsyncSession) -> BackfillResult:
    """遍历所有 User 落实"有且仅一个默认租户"，返回统计；调用方负责 commit。"""
    users = list((await db.execute(select(User))).scalars().all())
    created = fixed = skipped = 0
    for user in users:
        memberships = list(
            (await db.execute(select(TenantMembership).where(TenantMembership.user_id == user.id))).scalars().all()
        )
        if not memberships:
            await provision_personal_tenant(db, user=user)
            created += 1
        elif user.default_tenant_id is None:
            user.default_tenant_id = memberships[0].tenant_id
            await db.flush()
            fixed += 1
        else:
            skipped += 1
    return BackfillResult(created=created, fixed=fixed, skipped=skipped)


async def _run() -> None:
    async with async_session_maker() as db:
        result = await backfill_tenants(db)
        await db.commit()
        print(
            f"租户回填完成：新建 {result.created} 个 personal 租户，"
            f"补默认指针 {result.fixed} 个，跳过 {result.skipped} 个已就绪用户"
        )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
