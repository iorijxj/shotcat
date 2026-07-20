"""租户模型（多租户 M2 P0，纯加）。

- Tenant：租户；kind 仅是元数据（personal=1 人租户 / org=多成员），不分叉模型。
- TenantMembership：user 属于 tenant 的唯一权威，(tenant_id, user_id) 唯一，
  天然支持一人多租户 / B2B 多成员。

P0 阶段只落模型与建号路径，隔离底座（读过滤/写盖章）与门禁切换在 P2/P4。
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin

# 取值集中在此，避免散落的魔法字符串。
TENANT_KIND_PERSONAL = "personal"
TENANT_KIND_ORG = "org"
TENANT_KIND_SYSTEM = "system"
MEMBERSHIP_ROLE_OWNER = "owner"
MEMBERSHIP_ROLE_MEMBER = "member"
MEMBERSHIP_STATUS_ACTIVE = "active"

# 系统兜底租户：承接迁移期解析不到归属的历史孤儿聚合根（无主 project、
# created_by 空串的 provider 等），使 tenant_id 得以转 NOT NULL（多租户 M2 P4a）。
SYSTEM_TENANT_ID = "system"
SYSTEM_TENANT_NAME = "系统兜底租户"


class Tenant(Base, TimestampMixin):
    """租户表。"""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="租户 ID")
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="租户名")
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TENANT_KIND_PERSONAL, comment="personal / org"
    )


class TenantMembership(Base, TimestampMixin):
    """租户成员关系表：user 属于 tenant 的唯一权威。"""

    __tablename__ = "tenant_memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_tenant_user"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="成员关系 ID")
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True, comment="租户 ID"
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id"), nullable=False, index=True, comment="用户 ID"
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default=MEMBERSHIP_ROLE_MEMBER, comment="owner / member"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=MEMBERSHIP_STATUS_ACTIVE, comment="成员状态"
    )


__all__ = [
    "Tenant",
    "TenantMembership",
    "TENANT_KIND_PERSONAL",
    "TENANT_KIND_ORG",
    "TENANT_KIND_SYSTEM",
    "MEMBERSHIP_ROLE_OWNER",
    "MEMBERSHIP_ROLE_MEMBER",
    "MEMBERSHIP_STATUS_ACTIVE",
    "SYSTEM_TENANT_ID",
    "SYSTEM_TENANT_NAME",
]
