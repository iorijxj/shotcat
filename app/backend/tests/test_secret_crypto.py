"""Provider api_key/api_secret 加密工具与 ORM 透明加解密测试。"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.core.secret_crypto import decrypt_secret_or_passthrough, encrypt_secret
from app.models.llm import Provider, ProviderStatus

TENANT_ID = "test-tenant"


def test_encrypt_then_decrypt_roundtrips() -> None:
    ciphertext = encrypt_secret("sk-real-secret")
    assert ciphertext != "sk-real-secret"
    assert decrypt_secret_or_passthrough(ciphertext) == "sk-real-secret"


def test_decrypt_passthrough_for_legacy_plaintext() -> None:
    """存量明文（不是 Fernet token）解密失败时原样返回，不能直接报错。"""
    assert decrypt_secret_or_passthrough("legacy-plaintext-key") == "legacy-plaintext-key"


def test_encrypt_empty_string_stays_empty() -> None:
    assert encrypt_secret("") == ""
    assert decrypt_secret_or_passthrough("") == ""


async def _build_session():
    engine = create_async_engine("sqlite+aiosqlite://", future=True)
    session_local = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return session_local(), engine


@pytest.mark.asyncio
async def test_provider_api_key_is_encrypted_at_rest_and_transparent_on_read() -> None:
    db, engine = await _build_session()
    async with db:
        db.add(
            Provider(
                id="p-crypto",
                tenant_id=TENANT_ID,
                name="OpenAI",
                base_url="https://api.openai.com/v1",
                api_key="sk-real-secret",
                api_secret="",
                status=ProviderStatus.testing,
            )
        )
        await db.commit()

        # ORM 层：读出来应该是明文（透明解密）
        loaded = await db.get(Provider, "p-crypto")
        assert loaded is not None
        assert loaded.api_key == "sk-real-secret"

        # 用无类型信息的原生 SQL 查真正落库的值（TypeDecorator 绑定在列类型上，
        # ORM/Core 的类型化查询都会自动解密，只有 text() 原生查询才能看到真实存储值）：应为密文
        raw = (
            await db.execute(text("SELECT api_key FROM providers WHERE id = :id"), {"id": "p-crypto"})
        ).scalar_one()
        assert raw != "sk-real-secret"
    await engine.dispose()
