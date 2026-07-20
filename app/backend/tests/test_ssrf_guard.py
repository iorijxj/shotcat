"""SSRF 防护回归测试（安全整改阶段三 3.3）。

覆盖：内网/回环/链路本地/IPv6 各类目标拦截、非 http(s) scheme 拦截、
域名解析到内网（DNS rebinding 入口）拦截、连接后对端 IP 复核、
重定向拒绝跟随、以及本地开发开关放行。
"""

from __future__ import annotations

import socket
from types import SimpleNamespace

import httpx
import pytest

from app.config import settings
from app.core.ssrf_guard import (
    SSRFBlockedError,
    assert_response_addr_allowed,
    assert_url_allowed,
)
from app.utils import files as files_utils


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/steal",
        "http://10.0.0.5/internal",
        "http://172.16.3.4/internal",
        "http://192.168.1.1/router",
        "http://169.254.169.254/latest/meta-data/",  # 云元数据服务
        "http://[::1]/loopback",
        "http://[::ffff:10.0.0.1]/mapped",
        "http://0.0.0.0/unspecified",
        "http://100.64.0.1/cgnat",
    ],
)
def test_private_targets_blocked(url: str) -> None:
    with pytest.raises(SSRFBlockedError):
        assert_url_allowed(url)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://10.0.0.1/x", "gopher://x/1"])
def test_non_http_schemes_blocked(url: str) -> None:
    with pytest.raises(SSRFBlockedError):
        assert_url_allowed(url)


def test_hostname_resolving_to_private_ip_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """模拟 DNS rebinding 入口：域名看着正常，解析结果是内网 IP。"""

    def _fake_getaddrinfo(*args: object, **kwargs: object):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.20.30.40", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    with pytest.raises(SSRFBlockedError):
        assert_url_allowed("https://cdn.evil-provider.example/video.mp4")


def test_public_ip_allowed() -> None:
    assert_url_allowed("https://93.184.216.34/video.mp4")


def test_dev_switch_allows_private_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ssrf_allow_private_targets", True)
    assert_url_allowed("http://127.0.0.1/mock-provider")


def test_response_peer_addr_recheck_blocks_rebinding() -> None:
    """连接后对端 IP 落在内网（校验后被 rebind）时，响应内容必须被丢弃。"""
    stream = SimpleNamespace(get_extra_info=lambda key: ("10.0.0.5", 80) if key == "server_addr" else None)
    response = httpx.Response(200, extensions={"network_stream": stream})
    with pytest.raises(SSRFBlockedError):
        assert_response_addr_allowed(response)


def test_response_without_network_stream_is_tolerated() -> None:
    assert_response_addr_allowed(httpx.Response(200))


async def test_create_file_blocks_private_url_before_request() -> None:
    """集成点：create_file_from_url_or_b64 在发请求前就拦截内网 URL（session 不会被触碰）。"""
    with pytest.raises(SSRFBlockedError):
        await files_utils.create_file_from_url_or_b64(None, url="http://169.254.169.254/latest/meta-data/")


async def test_create_file_rejects_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    """3xx 不自动跟随：直接报错，而不是把重定向空 body 当文件落库。"""
    monkeypatch.setattr(settings, "ssrf_allow_private_targets", True)  # 跳过解析预检，走到 HTTP 层
    transport = httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"Location": "http://10.0.0.5/internal"})
    )
    orig_client = httpx.AsyncClient

    def _mocked_client(**kwargs: object) -> httpx.AsyncClient:
        return orig_client(transport=transport, **kwargs)

    monkeypatch.setattr(files_utils.httpx, "AsyncClient", _mocked_client)
    with pytest.raises(ValueError, match="重定向"):
        await files_utils.create_file_from_url_or_b64(None, url="http://provider.example/result.png")
