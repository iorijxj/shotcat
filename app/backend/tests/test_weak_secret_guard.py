"""弱口令启动校验回归测试（公众化 M1）。

面向公众部署前，检测后端实际拿到的敏感凭证（DB 口令 / S3 键 / Redis 口令 /
JWT 密钥 / Provider 加密密钥）是否仍是随仓库下发的默认/弱口令；命中即拒绝启动。
安全默认：allow_weak_secrets 缺省 False（生产强制强口令）；本机防火墙内开发用
ALLOW_WEAK_SECRETS=true 显式豁免，参考 auth_jwt_secret「缺失即报错」的范式。
"""

from __future__ import annotations

import pytest

from app.config import Settings

_FERNET = "45g4UuFvJzMwVL1wJ8EaqNOYhPiHDp-k3ZkThzz10A4="

# 一套全部强口令的基线，单个测试只把其中一项替换成弱口令。
_STRONG = {
    "allow_weak_secrets": False,
    "database_url": "mysql+aiomysql://jellyfish:Zx9-tR2pQw7Lm4Vk@db:3306/jellyfish",
    "s3_access_key_id": "AKIA7QEXAMPLESTRONGKEY",
    "s3_secret_access_key": "wJalrXUtnFEMI-K7MDENG-bPxRfiCYEXAMPLEKEY",
    "redis_password": "g3godIM9INCOR9uT2rr1ohVn",
    "auth_jwt_secret": "1ee56e82df9a4c51975cd691ca3435ee10f034acbda32e59861c88614f534094",
    "provider_secret_enc_key": _FERNET,
}


def test_strong_secrets_pass() -> None:
    Settings(**_STRONG)  # 不应抛错


def test_weak_db_password_rejected() -> None:
    cfg = {**_STRONG, "database_url": "mysql+aiomysql://jellyfish:change-me@db:3306/jellyfish"}
    with pytest.raises(ValueError, match="弱口令|DATABASE_URL|数据库"):
        Settings(**cfg)


def test_default_rustfs_access_key_rejected() -> None:
    cfg = {**_STRONG, "s3_access_key_id": "rustfsadmin"}
    with pytest.raises(ValueError, match="弱口令|S3"):
        Settings(**cfg)


def test_default_rustfs_secret_key_rejected() -> None:
    cfg = {**_STRONG, "s3_secret_access_key": "rustfsadmin"}
    with pytest.raises(ValueError, match="弱口令|S3"):
        Settings(**cfg)


def test_placeholder_jwt_secret_rejected() -> None:
    cfg = {**_STRONG, "auth_jwt_secret": "change-me-to-a-random-secret"}
    with pytest.raises(ValueError, match="弱口令|JWT"):
        Settings(**cfg)


def test_placeholder_provider_enc_key_rejected() -> None:
    cfg = {**_STRONG, "provider_secret_enc_key": "change-me-to-a-random-fernet-key"}
    with pytest.raises(ValueError, match="弱口令|PROVIDER"):
        Settings(**cfg)


def test_allow_weak_secrets_escape_hatch() -> None:
    cfg = {
        **_STRONG,
        "allow_weak_secrets": True,
        "database_url": "mysql+aiomysql://jellyfish:change-me@db:3306/jellyfish",
        "s3_access_key_id": "rustfsadmin",
        "s3_secret_access_key": "rustfsadmin",
    }
    Settings(**cfg)  # 显式豁免后不应抛错


def test_sqlite_default_without_password_passes() -> None:
    cfg = {**_STRONG, "database_url": "sqlite+aiosqlite:///./jellyfish.db"}
    Settings(**cfg)  # 无口令段，不应误判
