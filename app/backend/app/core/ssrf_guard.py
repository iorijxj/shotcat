"""SSRF 防护：校验外部下载 URL 不指向内网/本机（安全整改阶段三 3.3）。

攻击链背景：create_file_from_url_or_b64 下载的 url 来自 LLM 供应商返回的生成结果，
而登录用户可以创建指向自己服务器的 Provider——"假供应商"返回内网 URL 即可让后端
充当内网探测跳板。防护分两段：

1. 请求前 assert_url_allowed：scheme 白名单 + 解析主机名的全部 IP 逐个校验；
2. 响应后 assert_response_addr_allowed：校验实际连接到的对端 IP。解析校验与真正
   连接之间存在 DNS rebinding 窗口（先答公网 IP 通过校验、连接时再答内网 IP），
   事后校验保证即使被 rebind，响应内容也不会回流落库。

重定向不在这里处理：调用方禁用自动跟随并对 3xx 直接报错（见 utils/files.py），
不存在"302 跳内网"的绕过面。
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from app.config import settings


class SSRFBlockedError(ValueError):
    """下载目标位于禁止访问的网络范围。"""


_ALLOWED_SCHEMES = {"http", "https"}


def _is_forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    # 非公网一律拒绝：覆盖私有网段/回环/链路本地(169.254 含云元数据)/保留/CGNAT 等
    return not ip.is_global or ip.is_multicast


def assert_url_allowed(url: str) -> None:
    """请求发出前校验 URL；不通过时抛 SSRFBlockedError。

    注意 getaddrinfo 是同步阻塞调用（通常毫秒级）；调用点在任务执行链路里，
    偶发的慢 DNS 只影响该任务自身。
    """
    if settings.ssrf_allow_private_targets:
        return
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise SSRFBlockedError(f"URL scheme 不允许: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise SSRFBlockedError("URL 缺少主机名")
    try:
        addr_infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SSRFBlockedError(f"无法解析下载主机名: {host}") from exc
    for info in addr_infos:
        ip = ipaddress.ip_address(info[4][0])
        if _is_forbidden_ip(ip):
            raise SSRFBlockedError(f"下载目标指向禁止访问的网段: {host} -> {ip}")


def assert_response_addr_allowed(response: httpx.Response) -> None:
    """响应到手后校验实际连接的对端 IP，封堵 DNS rebinding 窗口。

    network_stream 扩展仅真实网络传输提供（MockTransport 等测试替身没有），
    缺失时跳过——请求前的解析校验仍然生效。
    """
    if settings.ssrf_allow_private_targets:
        return
    stream = response.extensions.get("network_stream")
    if stream is None:
        return
    server_addr = stream.get_extra_info("server_addr")
    if not server_addr:
        return
    ip = ipaddress.ip_address(server_addr[0])
    if _is_forbidden_ip(ip):
        raise SSRFBlockedError(f"实际连接地址位于禁止访问的网段: {ip}")
