"""迁移脚本回归测试：确保存量明文 api_key 会被真正加密。

抓的是一个真实缺陷——旧写法 `provider.api_key = provider.api_key`（读出解密后明文、赋回相同值）
不会触发 UPDATE，存量明文永远不会被加密。现改为 `flag_modified` 强制写回。
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models.llm  # noqa: F401  # 确保 metadata 注册
from app.cli.migrate_provider_secrets import encrypt_all_providers
from app.core.db import Base
from app.models.llm import Provider


def test_migrate_encrypts_legacy_plaintext() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def run():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # ORM 建一个 provider（会加密），再用 raw SQL 把 api_key 改回明文，模拟"存量未加密"
        async with maker() as db:
            db.add(Provider(id="p1", name="n", base_url="u", api_key="sk-x"))
            db.add(Provider(id="p2", name="n2", base_url="u2"))  # 空密钥
            await db.commit()
            await db.execute(text("UPDATE providers SET api_key = 'sk-plaintext' WHERE id = 'p1'"))
            await db.commit()
        # 迁移
        async with maker() as db:
            count = await encrypt_all_providers(db)
            await db.commit()
        # raw 读，确认 p1 已变 Fernet 密文
        async with maker() as db:
            raw = (await db.execute(text("SELECT api_key FROM providers WHERE id = 'p1'"))).scalar_one()
            raw2 = (await db.execute(text("SELECT api_key FROM providers WHERE id = 'p2'"))).scalar_one()
        await engine.dispose()
        return count, raw, raw2

    count, raw, raw2 = asyncio.run(run())
    assert count == 2
    assert raw.startswith("gAAAAA"), f"存量明文未被加密：{raw[:12]!r}"
    assert raw2 == ""  # 空密钥保持空
