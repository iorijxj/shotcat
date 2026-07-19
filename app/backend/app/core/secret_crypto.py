"""敏感字段静态加密（Fernet 对称加密）。

用于 Provider.api_key/api_secret 这类需要落库但不该明文存储的字段。
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


def _fernet() -> Fernet:
    return Fernet(settings.provider_secret_enc_key.encode("utf-8"))


def encrypt_secret(value: str) -> str:
    """加密；空字符串原样返回（Provider.api_key 默认值就是空串，不需要加密占位）。"""
    if not value:
        return value
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret_or_passthrough(value: str) -> str:
    """解密；解密失败（尚未迁移的存量明文数据）时原样返回，保证迁移前后都能正常读取。"""
    if not value:
        return value
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return value
