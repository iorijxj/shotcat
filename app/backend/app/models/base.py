"""通用模型混入。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """created_at / updated_at 时间戳混入。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class TenantScopedMixin:
    """聚合根的租户归属混入（多租户 M2）。

    P4c 起 tenant_id 转 NOT NULL 并打开 session 级读过滤（do_orm_execute）。
    所有带该混入的模型由 core/db.py 的 before_flush 事件在有租户上下文时自动盖章。
    子实体不加此混入，租户由其聚合根决定。存量 MySQL 由 ensure_root_tenant_not_null
    迁移 CLI 转换（P4a 已就位）；create_all 新库直接 NOT NULL。
    """

    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True, comment="所属租户 ID"
    )
