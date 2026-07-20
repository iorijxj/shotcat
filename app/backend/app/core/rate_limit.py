"""AI 生成类接口限流（安全整改阶段三 3.4）：防止 LLM/图片/视频生成被刷爆账单。

以 ASGI 中间件按"路径清单 + POST"匹配生成类端点，粒度为登录用户（JWT sub），
无有效 token 时回落到来源 IP（这类请求随后也会被登录门禁 401，回落只为兜底）。
滑动窗口计数在进程内存中（backend 为单进程 uvicorn，见 login_throttle 的说明）。

选择中间件而非逐端点挂依赖：生成类端点散在 film/studio/script_processing 三个
上游路由文件里，中间件把清单集中在这一个我们自己的文件里，不改任何上游路由。
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
]

_LIMITED_MESSAGE = "生成请求过于频繁，请稍后再试"

# key -> 窗口内的请求时间戳（整体替换而非原地修改）
_hits: dict[str, tuple[float, ...]] = {}


def _now() -> float:
    return time.monotonic()


def _is_generation_request(method: str, path: str) -> bool:
    if method != "POST":
        return False
    return any(p.match(path) for p in _GENERATION_PATH_PATTERNS)


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


def _consume(key: str) -> bool:
    """窗口内未超限则记一次并返回 True；超限返回 False。"""
    limit = settings.generation_rate_limit_per_minute
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


class GenerationRateLimitMiddleware:
    """纯 ASGI 中间件：命中生成类端点且超限时直接回 429（统一 ApiResponse 壳）。"""

    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http" or not _is_generation_request(scope.get("method", ""), scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        if not _consume(_caller_key(scope)):
            response = JSONResponse(
                status_code=429,
                content={"code": 429, "message": _LIMITED_MESSAGE, "data": None, "meta": None},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
