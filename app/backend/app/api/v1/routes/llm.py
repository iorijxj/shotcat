"""LLM 相关基础配置的 CRUD 接口：Provider / Model / ModelSettings。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_tenant, get_current_user, get_db
from app.models.auth import User
from app.models.llm import ModelCategoryKey
from app.services.auth.tenants import TenantContext
from app.schemas.common import ApiResponse, PaginatedData, created_response, empty_response, success_response
from app.services.auth.ownership import assert_model_owned, assert_provider_owned
from app.schemas.llm import (
    ImageGenerationOptionsRead,
    ModelCreate,
    ModelRead,
    ModelSettingsRead,
    ModelSettingsUpdate,
    ModelUpdate,
    ProviderCreate,
    ProviderRead,
    ProviderSupportedRead,
    VideoGenerationOptionsRead,
    ProviderUpdate,
)
from app.services.llm.manage import (
    create_model as create_model_service,
    create_provider as create_provider_service,
    delete_model as delete_model_service,
    delete_provider as delete_provider_service,
    get_model_settings as get_model_settings_service,
    get_image_generation_options as get_image_generation_options_service,
    get_video_generation_options as get_video_generation_options_service,
    list_supported_providers as list_supported_providers_service,
    list_models_paginated,
    list_providers_paginated,
    update_model as update_model_service,
    update_model_settings as update_model_settings_service,
    update_provider as update_provider_service,
)

router = APIRouter()

# 列表排序允许的字段（避免注入）
PROVIDER_ORDER_FIELDS = {"name", "created_at", "updated_at"}
MODEL_ORDER_FIELDS = {"name", "category", "created_at", "updated_at"}
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 100


# ---------- Provider ----------


@router.get(
    "/providers",
    response_model=ApiResponse[PaginatedData[ProviderRead]],
    summary="列出模型供应商（分页）",
)
async def list_providers(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
    q: str | None = Query(None, description="关键字，过滤 name/description"),
    order: str | None = Query(None, description="排序字段：name, created_at, updated_at"),
    is_desc: bool = Query(False, description="是否倒序"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="每页条数"),
) -> ApiResponse[PaginatedData[ProviderRead]]:
    return await list_providers_paginated(
        db,
        q=q,
        order=order,
        is_desc=is_desc,
        page=page,
        page_size=page_size,
        allow_fields=PROVIDER_ORDER_FIELDS,
        tenant_id=tenant.tenant_id,
    )


@router.get(
    "/providers/supported",
    response_model=ApiResponse[list[ProviderSupportedRead]],
    summary="列出系统支持的供应商能力",
)
async def list_supported_providers(
    category: ModelCategoryKey | None = Query(None, description="按模型类别过滤：text/image/video"),
) -> ApiResponse[list[ProviderSupportedRead]]:
    items = list_supported_providers_service(category=category)
    return success_response(items)


@router.get(
    "/image-generation-options",
    response_model=ApiResponse[ImageGenerationOptionsRead],
    summary="获取当前默认图片模型的关键帧规格选项",
)
async def get_image_generation_options(
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ImageGenerationOptionsRead]:
    data = await get_image_generation_options_service(db)
    return success_response(data)


@router.get(
    "/video-generation-options",
    response_model=ApiResponse[VideoGenerationOptionsRead],
    summary="获取当前默认视频模型的动态比例选项",
)
async def get_video_generation_options(
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[VideoGenerationOptionsRead]:
    data = await get_video_generation_options_service(db)
    return success_response(data)


@router.post(
    "/providers",
    response_model=ApiResponse[ProviderRead],
    status_code=status.HTTP_201_CREATED,
    summary="创建模型供应商",
)
async def create_provider(
    body: ProviderCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[ProviderRead]:
    provider = await create_provider_service(db, body=body, current_user_id=current_user.id)
    return created_response(ProviderRead.model_validate(provider))


@router.get(
    "/providers/{provider_id}",
    response_model=ApiResponse[ProviderRead],
    summary="获取单个模型供应商",
)
async def get_provider(
    provider_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[ProviderRead]:
    provider = await assert_provider_owned(db, provider_id=provider_id, current_user=current_user)
    return success_response(ProviderRead.model_validate(provider))


@router.patch(
    "/providers/{provider_id}",
    response_model=ApiResponse[ProviderRead],
    summary="更新模型供应商",
)
async def update_provider(
    provider_id: str,
    body: ProviderUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[ProviderRead]:
    await assert_provider_owned(db, provider_id=provider_id, current_user=current_user)
    provider = await update_provider_service(db, provider_id=provider_id, body=body)
    return success_response(ProviderRead.model_validate(provider))


@router.delete(
    "/providers/{provider_id}",
    response_model=ApiResponse[None],
    status_code=status.HTTP_200_OK,
    summary="删除模型供应商",
)
async def delete_provider(
    provider_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    await assert_provider_owned(db, provider_id=provider_id, current_user=current_user)
    await delete_provider_service(db, provider_id=provider_id)
    return empty_response()


# ---------- Model ----------


@router.get(
    "/models",
    response_model=ApiResponse[PaginatedData[ModelRead]],
    summary="列出模型（分页）",
)
async def list_models(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    tenant: TenantContext = Depends(get_current_tenant),
    provider_id: str | None = Query(None, description="按供应商过滤"),
    category: ModelCategoryKey | None = Query(None, description="按模型类别过滤"),
    q: str | None = Query(None, description="关键字，过滤 name/description"),
    order: str | None = Query(None, description="排序字段：name, category, created_at, updated_at"),
    is_desc: bool = Query(False, description="是否倒序"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="每页条数"),
) -> ApiResponse[PaginatedData[ModelRead]]:
    if provider_id is not None:
        await assert_provider_owned(db, provider_id=provider_id, current_user=current_user)
    return await list_models_paginated(
        db,
        provider_id=provider_id,
        category=category,
        q=q,
        order=order,
        is_desc=is_desc,
        page=page,
        page_size=page_size,
        allow_fields=MODEL_ORDER_FIELDS,
        tenant_id=tenant.tenant_id,
    )


@router.post(
    "/models",
    response_model=ApiResponse[ModelRead],
    status_code=status.HTTP_201_CREATED,
    summary="创建模型",
)
async def create_model(
    body: ModelCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[ModelRead]:
    await assert_provider_owned(db, provider_id=body.provider_id, current_user=current_user)
    model = await create_model_service(db, body=body)
    return created_response(ModelRead.model_validate(model))


@router.get(
    "/models/{model_id}",
    response_model=ApiResponse[ModelRead],
    summary="获取单个模型",
)
async def get_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[ModelRead]:
    model = await assert_model_owned(db, model_id=model_id, current_user=current_user)
    return success_response(ModelRead.model_validate(model))


@router.patch(
    "/models/{model_id}",
    response_model=ApiResponse[ModelRead],
    summary="更新模型",
)
async def update_model(
    model_id: str,
    body: ModelUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[ModelRead]:
    await assert_model_owned(db, model_id=model_id, current_user=current_user)
    if body.provider_id is not None:
        await assert_provider_owned(db, provider_id=body.provider_id, current_user=current_user)
    model = await update_model_service(db, model_id=model_id, body=body)
    return success_response(ModelRead.model_validate(model))


@router.delete(
    "/models/{model_id}",
    response_model=ApiResponse[None],
    status_code=status.HTTP_200_OK,
    summary="删除模型",
)
async def delete_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    await assert_model_owned(db, model_id=model_id, current_user=current_user)
    await delete_model_service(db, model_id=model_id)
    return empty_response()


# ---------- ModelSettings（单例） ----------


@router.get(
    "/model-settings",
    response_model=ApiResponse[ModelSettingsRead],
    summary="获取模型全局设置（单例）",
)
async def get_model_settings(
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ModelSettingsRead]:
    settings = await get_model_settings_service(db)
    return success_response(ModelSettingsRead.model_validate(settings))


@router.put(
    "/model-settings",
    response_model=ApiResponse[ModelSettingsRead],
    summary="更新模型全局设置（单例）",
)
async def update_model_settings(
    body: ModelSettingsUpdate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ModelSettingsRead]:
    settings = await update_model_settings_service(db, body=body)
    return success_response(ModelSettingsRead.model_validate(settings))
