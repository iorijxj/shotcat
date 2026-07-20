"""租户归属服务（多租户 M2 P0）。

- provision_personal_tenant：建号即建 tenant-of-one（tenant + owner membership +
  回写 user.default_tenant_id），供 CLI 建号与将来的注册复用。
- resolve_active_tenant：解析用户活跃租户，产出不可变 TenantContext。
  P0 只提供解析能力与单测，尚未接入路由、也不写 session（见方案 §6 P2/P4）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import User
from app.models.tenant import (
    MEMBERSHIP_ROLE_OWNER,
    MEMBERSHIP_STATUS_ACTIVE,
    TENANT_KIND_PERSONAL,
    Tenant,
    TenantMembership,
)


@dataclass(frozen=True)
class TenantContext:
    """请求维度的活跃租户上下文（轻量、不可变，不外泄 ORM 对象）。"""

    tenant_id: str
    role: str
    user_id: str


async def provision_personal_tenant(db: AsyncSession, *, user: User) -> Tenant:
    """为 user 建 1 人租户 + owner membership 并回写 default_tenant_id。

    要求 user 已 add+flush（membership.user_id 是 FK）。按 tenant→membership→回写
    的顺序落库，满足 MySQL 即时外键约束；调用方负责最终 commit。
    """
    tenant = Tenant(id=str(uuid.uuid4()), name=user.username, kind=TENANT_KIND_PERSONAL)
    db.add(tenant)
    await db.flush()
    db.add(
        TenantMembership(
            id=str(uuid.uuid4()),
            tenant_id=tenant.id,
            user_id=user.id,
            role=MEMBERSHIP_ROLE_OWNER,
            status=MEMBERSHIP_STATUS_ACTIVE,
        )
    )
    await db.flush()
    user.default_tenant_id = tenant.id
    await db.flush()
    return tenant


async def resolve_active_tenant(db: AsyncSession, user: User) -> TenantContext:
    """解析用户活跃租户：优先 default_tenant_id 对应的有效 membership，回落唯一 membership。

    无有效成员关系 → 403；多成员且无法确定默认 → 409（本期单默认租户，正常不该出现）。
    """
    stmt = select(TenantMembership).where(
        TenantMembership.user_id == user.id,
        TenantMembership.status == MEMBERSHIP_STATUS_ACTIVE,
    )
    memberships = list((await db.execute(stmt)).scalars().all())
    if not memberships:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="当前用户没有可用租户")

    chosen = None
    if user.default_tenant_id:
        chosen = next((m for m in memberships if m.tenant_id == user.default_tenant_id), None)
    if chosen is None:
        if len(memberships) == 1:
            chosen = memberships[0]
        else:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="无法确定活跃租户")

    return TenantContext(tenant_id=chosen.tenant_id, role=chosen.role, user_id=user.id)


__all__ = ["TenantContext", "provision_personal_tenant", "resolve_active_tenant"]
