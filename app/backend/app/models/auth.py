"""登录用户模型。"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin


class User(Base, TimestampMixin):
    """登录用户表。"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="用户 ID")
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True, comment="登录用户名")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False, comment="bcrypt 密码哈希")


__all__ = ["User"]
