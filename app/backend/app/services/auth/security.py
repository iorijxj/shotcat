"""密码哈希与 JWT 签发/校验。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.config import settings

_JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(*, user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(minutes=settings.auth_jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.auth_jwt_secret, algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> str:
    """返回 token 里的 user_id；无效/过期时抛 jwt 的异常，由调用方转换为 401。"""
    payload = jwt.decode(token, settings.auth_jwt_secret, algorithms=[_JWT_ALGORITHM])
    return payload["sub"]
