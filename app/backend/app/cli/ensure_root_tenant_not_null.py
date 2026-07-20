"""幂等迁移：把 8 个根实体的 tenant_id 转 NOT NULL（多租户 M2 P4a）。

P4 打开硬隔离前，根实体的 tenant_id 必须全部非空（读过滤/写盖章的地基）。
模型层的 nullable=False 在 P4c 随门禁/读过滤一起打开（届时全新库/测试经
create_all 直接建成 NOT NULL）；现有 MySQL 库靠本脚本 ALTER。

安全前置：先确认 8 张表都无 tenant_id 残留（否则 ALTER 失败），有残留则中止并
提示先跑 backfill_root_tenants + backfill_residual_tenants。SQLite 无法 MODIFY
COLUMN，非 MySQL 方言只做残留校验、不改列（部署库是 MySQL）。

幂等，可重复执行。用法：
    uv run python -m app.cli.ensure_root_tenant_not_null
"""

from __future__ import annotations

import asyncio

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from app.core.db import engine

# 8 个根实体表（继承 TenantScopedMixin）
_ROOT_TABLES = ("projects", "scenes", "props", "costumes", "actors", "characters", "providers", "models")


class RootTenantNullError(RuntimeError):
    """仍有 tenant_id 为空的根实体残留，无法转 NOT NULL；请先跑 backfill_residual_tenants。"""


def _null_tenant_counts(sync_conn: Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in _ROOT_TABLES:
        row = sync_conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE tenant_id IS NULL")).scalar_one()
        counts[table] = int(row)
    return counts


def ensure_root_tenant_not_null(sync_conn: Connection) -> list[str]:
    """校验无 NULL 残留后把 8 表 tenant_id 转 NOT NULL；返回实际执行 ALTER 的表名。"""
    counts = _null_tenant_counts(sync_conn)
    residual = {t: c for t, c in counts.items() if c > 0}
    if residual:
        detail = "、".join(f"{t}={c}" for t, c in residual.items())
        raise RootTenantNullError(
            f"仍有 tenant_id 为空的根实体残留（{detail}），"
            "请先跑 backfill_root_tenants + backfill_residual_tenants 再转 NOT NULL"
        )

    if sync_conn.dialect.name != "mysql":
        # SQLite 不支持 ALTER ... MODIFY COLUMN；全新库经 create_all 已是 NOT NULL。
        return []

    altered: list[str] = []
    inspector = inspect(sync_conn)
    for table in _ROOT_TABLES:
        column = next(c for c in inspector.get_columns(table) if c["name"] == "tenant_id")
        if column["nullable"]:
            sync_conn.execute(text(f"ALTER TABLE {table} MODIFY COLUMN tenant_id VARCHAR(64) NOT NULL"))
            altered.append(table)
    return altered


async def _run() -> None:
    async with engine.begin() as conn:
        altered = await conn.run_sync(ensure_root_tenant_not_null)
    if altered:
        print(f"已把以下表 tenant_id 转 NOT NULL：{'、'.join(altered)}")
    else:
        print("8 个根实体 tenant_id 均已是 NOT NULL（或非 MySQL 方言，交 create_all 处理），无需改动")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
