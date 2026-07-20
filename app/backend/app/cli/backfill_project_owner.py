"""一次性回填脚本：把 owner_id 为空的存量 Project 归属到指定用户。

背景：`projects.owner_id` 迁移只建了可空列，未回填存量数据（owner_id 为空的历史项目
按"公共资源"语义所有登录用户可见）。本脚本把这些无主项目一次性划归某个用户。

用法：
    uv run python -m app.cli.backfill_project_owner --owner <user_id>

只处理 owner_id 为空的项目，已有归属的不动，可重复执行，幂等。
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.core.db import async_session_maker
from app.models.auth import User
from app.models.studio import Project


async def _backfill(*, owner_id: str) -> None:
    async with async_session_maker() as db:
        user = await db.get(User, owner_id)
        if user is None:
            raise SystemExit(f"用户不存在: {owner_id}（先用 app.cli.create_user 建号）")
        projects = list((await db.execute(select(Project).where(Project.owner_id.is_(None)))).scalars().all())
        for project in projects:
            project.owner_id = owner_id
        await db.commit()
        print(f"已把 {len(projects)} 个无主 Project 的 owner_id 回填为 {owner_id}（{user.username}）")


def main() -> None:
    parser = argparse.ArgumentParser(description="把 owner_id 为空的存量 Project 回填到指定用户")
    parser.add_argument("--owner", required=True, help="回填归属到的用户 ID")
    args = parser.parse_args()
    asyncio.run(_backfill(owner_id=args.owner))


if __name__ == "__main__":
    main()
