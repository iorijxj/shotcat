"""PROVIDER_SECRET_ENC_KEY 轮换：用旧密钥解密、新密钥重新加密 Provider 的 api_key/api_secret。

背景（阶段四 4.3 / 迁移检查清单 M4）：`api_key`/`api_secret` 用 `PROVIDER_SECRET_ENC_KEY`
做 Fernet 对称加密（见 `app/core/secret_crypto.py`）。换密钥后，存量密文用新密钥解不开，
必须"先旧钥解密、再新钥加密"重写一遍，不能直接换 .env 里的值。

用法（迁移当天，换 .env 之前先跑本脚本）：
    uv run python -m app.cli.rekey_provider_secrets --old-key <旧KEY> --new-key <新KEY>
跑完后再把 .env 的 PROVIDER_SECRET_ENC_KEY 改成新值并重启服务。

实现要点：本脚本**绕过 ORM 的 EncryptedSecret 透明加解密**，直接用 raw SQL 读写密文列——
因为 TypeDecorator 只认当前 settings 里的单一密钥，用它读写会导致双重加密或解密失败。

⚠️ 不可重复执行、不可幂等：第二次跑会把已用新钥加密的密文当明文再加一层。
   跑之前务必先备份 providers 表（或整库快照）。存量明文（尚未加密的历史数据）会顺带用新钥加密。
"""

from __future__ import annotations

import argparse
import asyncio

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_maker


def _convert(raw: str | None, old_f: Fernet, new_f: Fernet) -> tuple[str | None, bool]:
    """把一个字段值从旧钥密文转成新钥密文。空值跳过；旧钥解不开的视为存量明文，顺带用新钥加密。"""
    if not raw:
        return raw, False
    try:
        plain = old_f.decrypt(raw.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        plain = raw
    return new_f.encrypt(plain.encode("utf-8")).decode("utf-8"), True


async def rekey_providers(db: AsyncSession, *, old_key: str, new_key: str) -> int:
    """核心逻辑（接受 db，便于测试）：重加密所有 Provider，返回被改写的行数。"""
    old_f = Fernet(old_key.encode("utf-8"))
    new_f = Fernet(new_key.encode("utf-8"))
    rows = (await db.execute(text("SELECT id, api_key, api_secret FROM providers"))).all()
    changed = 0
    for row_id, api_key, api_secret in rows:
        new_key_val, c1 = _convert(api_key, old_f, new_f)
        new_secret_val, c2 = _convert(api_secret, old_f, new_f)
        if c1 or c2:
            await db.execute(
                text("UPDATE providers SET api_key = :ak, api_secret = :sec WHERE id = :id"),
                {"ak": new_key_val, "sec": new_secret_val, "id": row_id},
            )
            changed += 1
    return changed


async def _run(*, old_key: str, new_key: str) -> None:
    async with async_session_maker() as db:
        changed = await rekey_providers(db, old_key=old_key, new_key=new_key)
        await db.commit()
        print(f"已用新密钥重加密 {changed} 个 Provider 的 api_key/api_secret。请把 .env 的 PROVIDER_SECRET_ENC_KEY 改为新值并重启服务。")


def main() -> None:
    parser = argparse.ArgumentParser(description="轮换 PROVIDER_SECRET_ENC_KEY：旧钥解密、新钥重加密存量密文")
    parser.add_argument("--old-key", required=True, help="当前（旧）PROVIDER_SECRET_ENC_KEY")
    parser.add_argument("--new-key", required=True, help="要换成的（新）PROVIDER_SECRET_ENC_KEY")
    args = parser.parse_args()
    try:
        Fernet(args.old_key.encode("utf-8"))
        Fernet(args.new_key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise SystemExit(f"密钥非法（需 Fernet base64 格式）：{exc}")
    if args.old_key == args.new_key:
        raise SystemExit("新旧密钥相同，无需轮换")
    asyncio.run(_run(old_key=args.old_key, new_key=args.new_key))


if __name__ == "__main__":
    main()
