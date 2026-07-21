"""阶段四 4.3：PROVIDER_SECRET_ENC_KEY 轮换脚本测试。

覆盖 _convert 纯函数（旧钥密文→新钥、存量明文→新钥、空值跳过）与 rekey_providers 端到端
（用旧钥加密入库→rekey→新钥可解、旧钥解不开、空密钥不动）。
"""

from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models.llm  # noqa: F401  # 确保 metadata 注册
from app.core import secret_crypto
from app.core.db import Base
from app.cli.rekey_provider_secrets import _convert, rekey_providers
from app.models.llm import Provider

TENANT_ID = "test-tenant"


def test_convert_reencrypts_old_to_new() -> None:
    old_f = Fernet(Fernet.generate_key())
    new_f = Fernet(Fernet.generate_key())
    cipher = old_f.encrypt(b"sk-secret").decode()
    out, changed = _convert(cipher, old_f, new_f)
    assert changed is True
    assert new_f.decrypt(out.encode()).decode() == "sk-secret"
    with pytest.raises(InvalidToken):
        old_f.decrypt(out.encode())


def test_convert_plaintext_gets_encrypted() -> None:
    """存量明文（旧钥解不开）应被顺带用新钥加密。"""
    old_f = Fernet(Fernet.generate_key())
    new_f = Fernet(Fernet.generate_key())
    out, changed = _convert("plain-text-key", old_f, new_f)
    assert changed is True
    assert new_f.decrypt(out.encode()).decode() == "plain-text-key"


def test_convert_empty_skipped() -> None:
    old_f = Fernet(Fernet.generate_key())
    new_f = Fernet(Fernet.generate_key())
    assert _convert("", old_f, new_f) == ("", False)
    assert _convert(None, old_f, new_f) == (None, False)


def test_rekey_providers_end_to_end(monkeypatch) -> None:
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    engine = create_async_engine("sqlite+aiosqlite://")
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def run():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # 用旧钥加密写入：monkeypatch settings 让 EncryptedSecret 走 old_key
        monkeypatch.setattr(secret_crypto.settings, "provider_secret_enc_key", old_key)
        async with maker() as db:
            db.add(
                Provider(
                    id="p1",
                    tenant_id=TENANT_ID,
                    name="prov1",
                    base_url="https://x",
                    api_key="sk-a",
                    api_secret="sec-a",
                )
            )
            db.add(Provider(id="p2", tenant_id=TENANT_ID, name="prov2", base_url="https://y"))  # 空密钥
            await db.commit()
        async with maker() as db:
            changed = await rekey_providers(db, old_key=old_key, new_key=new_key)
            await db.commit()
        async with maker() as db:
            rows = {
                r[0]: (r[1], r[2])
                for r in (await db.execute(text("SELECT id, api_key, api_secret FROM providers"))).all()
            }
        await engine.dispose()
        return changed, rows

    changed, rows = asyncio.run(run())
    assert changed == 1  # 只有 p1 有非空密钥
    new_f = Fernet(new_key.encode())
    old_f = Fernet(old_key.encode())
    ak, sec = rows["p1"]
    assert new_f.decrypt(ak.encode()).decode() == "sk-a"
    assert new_f.decrypt(sec.encode()).decode() == "sec-a"
    with pytest.raises(InvalidToken):
        old_f.decrypt(ak.encode())  # 旧钥再也解不开
    assert rows["p2"] == ("", "")  # 空密钥不动
