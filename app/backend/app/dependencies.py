"""FastAPI 依赖注入。"""

import uuid
from collections.abc import AsyncGenerator

import jwt
from fastapi import Depends, Header, HTTPException, status
from langchain_core.language_models.chat_models import BaseChatModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.db import async_session_maker
from app.models.auth import User
from app.services.auth.security import decode_access_token, hash_password
from app.services.auth.tenants import TenantContext, provision_personal_tenant, resolve_active_tenant
from app.services.llm.resolver import build_default_text_llm

# 登录鉴权临时旁路（AUTH_DISABLED=true）用的固定开发态用户名，自动创建/复用。
_DEV_BYPASS_USERNAME = "dev-local"


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """提供异步数据库会话。"""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def _get_or_create_dev_bypass_user(db: AsyncSession) -> User:
    """AUTH_DISABLED=true 时使用的固定开发态用户，首次访问自动创建，之后复用。

    和 cli/create_user.py 建号一样需要 provision_personal_tenant，否则
    get_current_tenant 会因为没有 membership 而 403（当前用户没有可用租户）。
    """
    stmt = select(User).where(User.username == _DEV_BYPASS_USERNAME)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is not None:
        return user
    user = User(id=str(uuid.uuid4()), username=_DEV_BYPASS_USERNAME, password_hash=hash_password(uuid.uuid4().hex))
    db.add(user)
    try:
        await db.flush()
        await provision_personal_tenant(db, user=user)
    except IntegrityError:
        # 首次并发请求同时建号，退回去复用先建成功的那条。
        await db.rollback()
        user = (await db.execute(stmt)).scalar_one_or_none()
        if user is None:
            raise
    return user


async def get_current_user(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """解析 Authorization: Bearer <token>，返回当前登录用户；缺失/无效/过期一律 401。

    AUTH_DISABLED=true 时临时旁路（内部开发用，待接入平台统一认证后移除）。
    """
    if settings.auth_disabled:
        return await _get_or_create_dev_bypass_user(db)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    token = authorization.split(" ", 1)[1].strip()
    try:
        user_id = decode_access_token(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def get_current_tenant(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TenantContext:
    """解析当前请求的活跃租户，并把 tenant_id 写入 session.info（多租户 M2）。

    session.info["tenant_id"] 供 core/db.py 的 before_flush 盖章与 do_orm_execute 读过滤
    读取。P4c 起已作为 api/v1 四个路由组的门禁（api/v1/__init__.py），请求进来即盖好租户。
    换外部认证时身份仍从 get_current_user 单一出口来，这里的解析分支随之调整即可。
    """
    ctx = await resolve_active_tenant(db, current_user)
    db.info["tenant_id"] = ctx.tenant_id
    return ctx


async def get_llm(db: AsyncSession = Depends(get_db)) -> BaseChatModel:
    """提供默认文本 LLM（ChatOpenAI）。"""
    return await build_default_text_llm(db, thinking=True)

async def get_nothinking_llm(db: AsyncSession = Depends(get_db)) -> BaseChatModel:
    """提供默认文本 LLM（ChatOpenAI，禁用 thinking）。"""
    return await build_default_text_llm(db, thinking=False)


class _ImageHttpRunnable:
    """最小图片生成 runnable：从环境变量读取配置，通过 HTTP 调用外部图片生成服务。"""

    def __init__(self, *, base_url: str, api_key: str, timeout_s: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_s = timeout_s

    def invoke(self, payload: dict) -> dict:  # noqa: ANN001
        try:
            import httpx
        except ImportError as e:  # pragma: no cover
            raise HTTPException(status_code=503, detail="Install httpx to enable image generation") from e
        with httpx.Client(timeout=self._timeout_s) as client:
            r = client.post(
                self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else {"images": data}

    async def ainvoke(self, payload: dict) -> dict:  # noqa: ANN001
        try:
            import httpx
        except ImportError as e:  # pragma: no cover
            raise HTTPException(status_code=503, detail="Install httpx to enable image generation") from e
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            r = await client.post(
                self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else {"images": data}
