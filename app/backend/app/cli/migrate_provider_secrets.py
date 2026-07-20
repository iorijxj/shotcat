"""一次性迁移脚本：把存量明文 api_key/api_secret 转成加密存储，可选回填 created_by。

用法：
    uv run python -m app.cli.migrate_provider_secrets
    uv run python -m app.cli.migrate_provider_secrets --assign-owner <user_id>

不传 --assign-owner 时只做加密迁移，不动 created_by（保持"迁移期公共资源"语义，
所有登录用户都能访问，直到手动指定归属）。可重复执行，幂等。
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.db import async_session_maker
from app.models.llm import Provider


async def encrypt_all_providers(db: AsyncSession, *, assign_owner: str | None = None) -> int:
    """把所有 Provider 的 api_key/api_secret 用当前密钥加密写回（幂等），可选回填 created_by，返回处理数。

    关键：强制 `flag_modified` 标记这两列为 dirty，flush 时 EncryptedSecret 才会加密写回。
    不能只靠 `provider.api_key = provider.api_key`——读出的是解密/透传后的明文，赋回相同值时
    SQLAlchemy 判定无变化、不会 UPDATE，存量明文就永远不会被加密。
    """
    providers = list((await db.execute(select(Provider))).scalars().all())
    for provider in providers:
        flag_modified(provider, "api_key")
        flag_modified(provider, "api_secret")
        if assign_owner and not provider.created_by:
            provider.created_by = assign_owner
    return len(providers)


async def _migrate(*, assign_owner: str | None) -> None:
    async with async_session_maker() as db:
        count = await encrypt_all_providers(db, assign_owner=assign_owner)
        await db.commit()
        suffix = f"，created_by 已回填为 {assign_owner}" if assign_owner else ""
        print(f"已处理 {count} 个 Provider：api_key/api_secret 已确保加密存储{suffix}")


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移存量 Provider 的明文密钥为加密存储，可选回填 created_by")
    parser.add_argument("--assign-owner", default=None, help="把 created_by 为空的存量 Provider 回填为该用户 ID")
    args = parser.parse_args()
    asyncio.run(_migrate(assign_owner=args.assign_owner))


if __name__ == "__main__":
    main()
