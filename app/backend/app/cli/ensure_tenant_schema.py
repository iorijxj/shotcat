"""幂等迁移：把 M2 P0 的租户 schema 落到现有库。

背景：P0 给 User 加了映射列 default_tenant_id，但 create_all 只建新表、不给
已存在的 users 表补列，现有 MySQL 库真实跑 app 时登录 SELECT 会命中不存在的列。
本脚本 create_all 建新表（tenants/tenant_memberships）+ 检测 users 缺列则补上，
可重复执行、幂等。P1 存量回填在此之后跑。

用法：
    uv run python -m app.cli.ensure_tenant_schema

注：只加可空列，不加 FK 约束（fresh create_all 会带 FK，迁移库的 FK 交给
P2 引入 Alembic 时统一对齐；P0 只求现有库能跑起来、不破坏行为）。
"""

from __future__ import annotations

import asyncio

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from app.core.db import Base, engine


def _sync_ensure(sync_conn: Connection) -> None:
    # 导入全部模型，填充 Base.metadata，使 create_all 覆盖到租户新表
    import app.models.auth  # noqa: F401
    import app.models.tenant  # noqa: F401
    import app.models.llm  # noqa: F401
    import app.models.studio  # noqa: F401
    import app.models.task  # noqa: F401
    import app.models.task_links  # noqa: F401

    Base.metadata.create_all(sync_conn)

    columns = {c["name"] for c in inspect(sync_conn).get_columns("users")}
    if "default_tenant_id" in columns:
        print("users.default_tenant_id 已存在，跳过")
        return
    sync_conn.execute(text("ALTER TABLE users ADD COLUMN default_tenant_id VARCHAR(64) NULL"))
    print("已给 users 补列 default_tenant_id")


async def _ensure() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_sync_ensure)
    print("租户 schema 已就绪（tenants / tenant_memberships / users.default_tenant_id）")


def main() -> None:
    asyncio.run(_ensure())


if __name__ == "__main__":
    main()
