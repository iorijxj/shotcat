"""幂等迁移：把四类资产 project_id 转 NOT NULL（多租户 M2 P3-D）。

P3 清理掉 project_id 为空的历史公共资产后，scene/prop/costume/actor 的
project_id 应转 NOT NULL，彻底取消跨租户共享语义。模型已改 nullable=False，
全新库/测试经 create_all 直接建成 NOT NULL；现有 MySQL 库靠本脚本 ALTER。

安全前置：先确认四张表都无 project_id 为空的残留（否则 ALTER 会失败），有
残留则中止并提示先跑 purge_public_assets。SQLite 无法 MODIFY COLUMN，非 MySQL
方言只做残留校验、不改列（部署库是 MySQL；SQLite 走 fresh create_all）。

幂等，可重复执行。用法：
    uv run python -m app.cli.ensure_asset_project_not_null
"""

from __future__ import annotations

import asyncio

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from app.core.db import engine

_ASSET_TABLES = ("scenes", "props", "costumes", "actors")


class AssetProjectNullError(RuntimeError):
    """仍有 project_id 为空的资产残留，无法转 NOT NULL；请先跑 purge_public_assets。"""


def _null_project_counts(sync_conn: Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in _ASSET_TABLES:
        row = sync_conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE project_id IS NULL")).scalar_one()
        counts[table] = int(row)
    return counts


def ensure_asset_project_not_null(sync_conn: Connection) -> list[str]:
    """校验无 NULL 残留后把四表 project_id 转 NOT NULL；返回实际执行 ALTER 的表名。"""
    counts = _null_project_counts(sync_conn)
    residual = {t: c for t, c in counts.items() if c > 0}
    if residual:
        detail = "、".join(f"{t}={c}" for t, c in residual.items())
        raise AssetProjectNullError(
            f"仍有 project_id 为空的资产残留（{detail}），请先跑 purge_public_assets 再转 NOT NULL"
        )

    if sync_conn.dialect.name != "mysql":
        # SQLite 不支持 ALTER ... MODIFY COLUMN；全新库经 create_all 已是 NOT NULL。
        return []

    altered: list[str] = []
    inspector = inspect(sync_conn)
    for table in _ASSET_TABLES:
        column = next(c for c in inspector.get_columns(table) if c["name"] == "project_id")
        if column["nullable"]:
            sync_conn.execute(text(f"ALTER TABLE {table} MODIFY COLUMN project_id VARCHAR(64) NOT NULL"))
            altered.append(table)
    return altered


async def _run() -> None:
    async with engine.begin() as conn:
        altered = await conn.run_sync(ensure_asset_project_not_null)
    if altered:
        print(f"已把以下表 project_id 转 NOT NULL：{'、'.join(altered)}")
    else:
        print("四类资产 project_id 均已是 NOT NULL（或非 MySQL 方言，交 create_all 处理），无需改动")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
