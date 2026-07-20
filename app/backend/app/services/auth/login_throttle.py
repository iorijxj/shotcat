"""登录防暴力破解：按用户名与来源 IP 的失败计数锁定（安全整改阶段三 3.1）。

内存实现的依据：backend 以单进程 uvicorn 运行（deploy/docker/backend.Dockerfile 无
--workers），进程内计数即全局精确。部署场景所有请求经 Caddy 反代到达，client IP
退化为网关地址，此时 IP 维度等效于"全局失败次数兜底"，故 IP 阈值明显高于用户名阈值，
避免共享出口误锁正常用户。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace

from fastapi import HTTPException, status

from app.config import settings

# 条目数超过该值时触发一次惰性全量清理，防止撞库造 key 导致内存膨胀
_MAX_ENTRIES = 10_000

_LOCKED_MESSAGE = "Too many failed login attempts, please try again later"


@dataclass(frozen=True)
class _Entry:
    count: int
    window_start: float
    locked_until: float


_entries: dict[str, _Entry] = {}


def _now() -> float:
    return time.monotonic()


def _is_expired(entry: _Entry, now: float, window: float) -> bool:
    return entry.locked_until <= now and now - entry.window_start > window


def _prune(now: float, window: float) -> None:
    if len(_entries) < _MAX_ENTRIES:
        return
    for key in [k for k, v in _entries.items() if _is_expired(v, now, window)]:
        _entries.pop(key, None)


def assert_login_not_locked(*, username: str, client_ip: str) -> None:
    """锁定期内直接拒绝，不进入密码校验；统一 429，不泄露锁的是用户名还是 IP。"""
    now = _now()
    for key in (f"user:{username}", f"ip:{client_ip}"):
        entry = _entries.get(key)
        if entry is not None and entry.locked_until > now:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=_LOCKED_MESSAGE)


def record_login_failure(*, username: str, client_ip: str) -> None:
    """记一次失败；达到阈值即锁定对应维度。"""
    now = _now()
    window = float(settings.login_lockout_seconds)
    _prune(now, window)
    for key, threshold in (
        (f"user:{username}", settings.login_max_failures_per_user),
        (f"ip:{client_ip}", settings.login_max_failures_per_ip),
    ):
        entry = _entries.get(key)
        if entry is None or _is_expired(entry, now, window):
            entry = _Entry(count=0, window_start=now, locked_until=0.0)
        entry = replace(entry, count=entry.count + 1)
        if entry.count >= threshold:
            entry = _Entry(count=0, window_start=now, locked_until=now + window)
        _entries[key] = entry


def record_login_success(*, username: str) -> None:
    """登录成功清零该用户名的失败计数；IP 计数保留（防止拿合法账号周期性清洗 IP 维度）。"""
    _entries.pop(f"user:{username}", None)


def reset_login_throttle() -> None:
    """仅供测试重置状态。"""
    _entries.clear()
