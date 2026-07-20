"""登录用户模型。"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin

# 与 User.default_tenant_id 的 FK 目标同批注册，保证 mapper 配置时 tenants 表已在
# 元数据里（tenant.py 只依赖 db/base，不回头 import auth，无循环）。
import app.models.tenant  # noqa: F401,E402


class User(Base, TimestampMixin):
    """登录用户表。"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="用户 ID")
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True, comment="登录用户名")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False, comment="bcrypt 密码哈希")
    # 活跃租户便捷指针（多租户 M2 P0，可空、可重算）；membership 才是归属唯一权威。
    default_tenant_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=True, comment="默认/活跃租户 ID"
    )


__all__ = ["User"]
