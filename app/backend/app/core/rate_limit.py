"""应用层接口限流：防止生成类接口刷爆账单、防止登录接口被洪泛（M1 扩展）。

以 ASGI 中间件按"路径清单 + POST"匹配受限端点：
- 生成类（安全整改阶段三 3.4）：粒度为登录用户（JWT sub），无有效 token 时回落
  来源 IP；阈值 generation_rate_limit_per_minute。
- 登录（公众化 M1）：粒度为来源 IP；阈值 login_rate_limit_per_minute。
两类各用独立键命名空间，互不串账。滑动窗口计数在进程内存中（backend 为单进程
uvicorn，见 login_throttle 的说明）。

选择中间件而非逐端点挂依赖：受限端点散在多个上游路由文件里，中间件把清单集中在
这一个我们自己的文件里，不改任何上游路由。
"""

from __future__ import annotations

import re
import time

from starlette.responses import JSONResponse

from app.config import settings

_WINDOW_SECONDS = 60.0
_MAX_ENTRIES = 10_000

# 生成类端点清单（均为 POST）。锚定到结尾，天然排除 */cancel、*/preview-prompt、
# */render-prompt 与状态查询类路径。
_GENERATION_PATH_PATTERNS = [
    re.compile(r"^/api/v1/script-processing/[^/]+$"),
    re.compile(r"^/api/v1/studio/image-tasks/(asset-batches|frame-batches)$"),
    re.compile(r"^/api/v1/studio/image-tasks/actors/[^/]+/image-tasks$"),
    re.compile(r"^/api/v1/studio/image-tasks/assets/[^/]+/[^/]+/image-tasks$"),
    re.compile(r"^/api/v1/studio/image-tasks/characters/[^/]+/image-tasks$"),
    re.compile(r"^/api/v1/studio/image-tasks/shot/[^/]+/frame-image-tasks$"),
    re.compile(r"^/api/v1/film/tasks/video$"),
    re.compile(r"^/api/v1/film/tasks/shot-frame-prompts$"),
    re.compile(r"^/api/v1/llm/providers/test-connection$"),
]

_LOGIN_PATH_PATTERN = re.compile(r"^/api/v1/auth/login$")

_GENERATION_LIMITED_MESSAGE = "生成请求过于频繁，请稍后再试"
_LOGIN_LIMITED_MESSAGE = "登录请求过于频繁，请稍后再试"

# key -> 窗口内的请求时间戳（整体替换而非原地修改）
_hits: dict[str, tuple[float, ...]] = {}


def _now() -> float:
    return time.monotonic()


def _caller_key(scope: dict) -> str:
    headers = dict(scope.get("headers") or [])
    auth = (headers.get(b"authorization") or b"").decode("latin-1")
    if auth.lower().startswith("bearer "):
        from app.services.auth.security import decode_access_token

        try:
            return f"user:{decode_access_token(auth[7:])}"
        except Exception:  # noqa: BLE001  # 无效 token 回落 IP，后续登录门禁会给 401
            pass
    client = scope.get("client")
    return f"ip:{client[0] if client else 'unknown'}"


def _prune(now: float) -> None:
    if len(_hits) < _MAX_ENTRIES:
        return
    for key in [k for k, v in _hits.items() if not v or now - v[-1] > _WINDOW_SECONDS]:
        _hits.pop(key, None)


def _classify(scope: dict) -> tuple[str, int, str] | None:
    """把请求归类到受限类别，返回 (计数键, 阈值, 超限提示)；不受限返回 None。"""
    if scope.get("method") != "POST":
        return None
    path = scope.get("path", "")
    if any(p.match(path) for p in _GENERATION_PATH_PATTERNS):
        return (f"gen:{_caller_key(scope)}", settings.generation_rate_limit_per_minute, _GENERATION_LIMITED_MESSAGE)
    if _LOGIN_PATH_PATTERN.match(path):
        client = scope.get("client")
        ip = client[0] if client else "unknown"
        return (f"login:{ip}", settings.login_rate_limit_per_minute, _LOGIN_LIMITED_MESSAGE)
    return None


def _consume(key: str, limit: int) -> bool:
    """窗口内未超限则记一次并返回 True；超限返回 False。"""
    if limit <= 0:
        return True
    now = _now()
    _prune(now)
    recent = tuple(t for t in _hits.get(key, ()) if now - t < _WINDOW_SECONDS)
    if len(recent) >= limit:
        _hits[key] = recent
        return False
    _hits[key] = recent + (now,)
    return True


def reset_generation_rate_limit() -> None:
    """仅供测试重置状态。"""
    _hits.clear()


class RateLimitMiddleware:
    """纯 ASGI 中间件：命中受限端点（生成/登录）且超限时直接回 429（统一 ApiResponse 壳）。"""

    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        hit = _classify(scope) if scope["type"] == "http" else None
        if hit is None:
            await self.app(scope, receive, send)
            return
        key, limit, message = hit
        if not _consume(key, limit):
            response = JSONResponse(
                status_code=429,
                content={"code": 429, "message": message, "data": None, "meta": None},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
