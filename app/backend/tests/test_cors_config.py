"""CORS 配置回归测试（公众化 M1）。

校验 cors_origins_list 解析与「携带凭证时拒绝通配 *」的启动期 fail-fast。
main.py 固定 allow_credentials=True，此时 origins 含 * 既被浏览器拒发凭证、
又等同放开全网，必须在启动时直接拒绝而非静默降级。
"""

from __future__ import annotations

import pytest

from app.config import Settings

# 构造 Settings 时 .env 仍会被读取，这里补齐两个无默认值的必填密钥，
# 使测试不依赖运行环境的 .env 是否存在。
_REQUIRED = {
    "auth_jwt_secret": "x" * 32,
    "provider_secret_enc_key": "45g4UuFvJzMwVL1wJ8EaqNOYhPiHDp-k3ZkThzz10A4=",
    "allow_weak_secrets": True,
}


def test_comma_separated_origins_parsed() -> None:
    s = Settings(cors_origins="https://a.example.com, https://b.example.com", **_REQUIRED)
    assert s.cors_origins_list == ["https://a.example.com", "https://b.example.com"]


def test_json_array_origins_parsed() -> None:
    s = Settings(cors_origins='["https://a.example.com","https://b.example.com"]', **_REQUIRED)
    assert s.cors_origins_list == ["https://a.example.com", "https://b.example.com"]


def test_empty_origins_is_fail_closed() -> None:
    s = Settings(cors_origins="", **_REQUIRED)
    assert s.cors_origins_list == []


def test_wildcard_origin_rejected_at_startup() -> None:
    with pytest.raises(ValueError, match="CORS"):
        Settings(cors_origins="*", **_REQUIRED)


def test_wildcard_mixed_with_domains_rejected() -> None:
    with pytest.raises(ValueError, match="CORS"):
        Settings(cors_origins="https://a.example.com,*", **_REQUIRED)
