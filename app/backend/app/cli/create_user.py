"""创建登录账号的命令行工具。

用法：
    uv run python -m app.cli.create_user --username xxx --password xxx
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from sqlalchemy import select

from app.core.db import async_session_maker
from app.models.auth import User
from app.services.auth.security import hash_password


async def _create_user(*, username: str, password: str) -> None:
    async with async_session_maker() as db:
        existing = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if existing is not None:
            raise SystemExit(f"用户名已存在: {username}")
        user = User(id=str(uuid.uuid4()), username=username, password_hash=hash_password(password))
        db.add(user)
        await db.commit()
        print(f"已创建用户 {username}（id={user.id}）")


def main() -> None:
    parser = argparse.ArgumentParser(description="创建登录账号")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()
    asyncio.run(_create_user(username=args.username, password=args.password))


if __name__ == "__main__":
    main()
