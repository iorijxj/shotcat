"""API v1 路由聚合。"""

from fastapi import APIRouter, Depends

from app.api.v1.routes import auth, film, health, llm, studio, script_processing
from app.dependencies import get_current_user

router = APIRouter()

router.include_router(health.router, tags=["health"])
# 登录/当前用户信息不挂登录门禁，其余路由都要求已登录（Depends(get_current_user)）
router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(film.router, prefix="/film", tags=["film"], dependencies=[Depends(get_current_user)])
router.include_router(llm.router, prefix="/llm", tags=["llm"], dependencies=[Depends(get_current_user)])
router.include_router(studio.router, prefix="/studio", dependencies=[Depends(get_current_user)])
router.include_router(script_processing.router, dependencies=[Depends(get_current_user)])
