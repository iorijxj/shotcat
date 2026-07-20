"""登录鉴权：登录、当前用户信息。这两个接口不挂全局登录门禁。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db
from app.models.auth import User
from app.schemas.auth import LoginRequest, TokenResponse, UserRead
from app.schemas.common import ApiResponse, success_response
from app.services.auth.login_throttle import (
    assert_login_not_locked,
    record_login_failure,
    record_login_success,
)
from app.services.auth.security import create_access_token, verify_password

router = APIRouter()


@router.post("/login", response_model=ApiResponse[TokenResponse], summary="登录")
async def login(
    body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> ApiResponse[TokenResponse]:
    client_ip = request.client.host if request.client else "unknown"
    assert_login_not_locked(username=body.username, client_ip=client_ip)
    stmt = select(User).where(User.username == body.username)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        record_login_failure(username=body.username, client_ip=client_ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    record_login_success(username=body.username)
    token = create_access_token(user_id=user.id)
    return success_response(TokenResponse(access_token=token))


@router.get("/me", response_model=ApiResponse[UserRead], summary="当前登录用户")
async def me(current_user: User = Depends(get_current_user)) -> ApiResponse[UserRead]:
    return success_response(UserRead.model_validate(current_user))
