"""Project CRUD。"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.utils import apply_keyword_filter, apply_order, paginate
from app.dependencies import get_current_tenant, get_current_user, get_db
from app.models.auth import User
from app.models.studio import Project
from app.services.auth.tenants import TenantContext
from app.models.types import ProjectStyle, ProjectVisualStyle
from app.schemas.common import ApiResponse, PaginatedData, created_response, empty_response, paginated_response, success_response
from app.services.auth.ownership import require_project_owner
from app.services.common import (
    create_and_refresh,
    entity_already_exists,
    ensure_not_exists,
    flush_and_refresh,
    patch_model,
)
from app.schemas.studio.projects import (
    ProjectCreate,
    ProjectRead,
    ProjectStyleOptionsRead,
    ProjectUpdate,
    StyleOption,
)
from app.services.studio.asset_export import list_project_asset_export_items
from app.services.studio.keyframe_export import iter_file_archive, iter_keyframe_archive, list_project_keyframe_export_items

router = APIRouter()

PROJECT_ORDER_FIELDS = {"name", "created_at", "updated_at", "progress"}


def _build_project_style_options() -> tuple[dict[ProjectVisualStyle, list[ProjectStyle]], dict[ProjectVisualStyle, ProjectStyle]]:
    mapping: dict[ProjectVisualStyle, list[ProjectStyle]] = {key: [] for key in ProjectVisualStyle}
    for item in ProjectStyle:
        if item.name.startswith("real_people_"):
            mapping[ProjectVisualStyle.live_action].append(item)
            continue
        if item.name.startswith("anime_") or item.name in {"guoman", "ink_wash"}:
            mapping[ProjectVisualStyle.anime].append(item)
            continue
    defaults: dict[ProjectVisualStyle, ProjectStyle] = {
        visual: styles[0]
        for visual, styles in mapping.items()
        if styles
    }
    return mapping, defaults


def _validate_project_style_combo(*, visual_style: ProjectVisualStyle, style: ProjectStyle) -> None:
    mapping, _defaults = _build_project_style_options()
    allowed = mapping.get(visual_style, [])
    if style not in allowed:
        raise ValueError(
            f"style is not allowed for visual_style: visual_style={visual_style.value}, "
            f"style={style.value}, allowed={[item.value for item in allowed]}"
        )


@router.get(
    "/style-options",
    response_model=ApiResponse[ProjectStyleOptionsRead],
    summary="获取项目风格候选项",
)
async def get_project_style_options(
) -> ApiResponse[ProjectStyleOptionsRead]:
    mapping, defaults = _build_project_style_options()
    data = ProjectStyleOptionsRead(
        visual_styles=[StyleOption(value=x.value, label=x.value) for x in ProjectVisualStyle],
        styles_by_visual_style={
            visual.value: [StyleOption(value=style.value, label=style.value) for style in styles]
            for visual, styles in mapping.items()
        },
        default_style_by_visual_style={visual.value: style.value for visual, style in defaults.items()},
    )
    return success_response(data)


@router.get(
    "",
    response_model=ApiResponse[PaginatedData[ProjectRead]],
    summary="项目列表（分页）",
)
async def list_projects(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
    q: str | None = Query(None, description="关键字，过滤 name/description"),
    order: str | None = Query(None, description="排序字段"),
    is_desc: bool = Query(False, description="是否倒序"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
) -> ApiResponse[PaginatedData[ProjectRead]]:
    stmt = select(Project).where(Project.tenant_id == tenant.tenant_id)
    stmt = apply_keyword_filter(stmt, q=q, fields=[Project.name, Project.description])
    stmt = apply_order(stmt, model=Project, order=order, is_desc=is_desc, allow_fields=PROJECT_ORDER_FIELDS, default="created_at")
    items, total = await paginate(db, stmt=stmt, page=page, page_size=page_size)
    return paginated_response([ProjectRead.model_validate(x) for x in items], page=page, page_size=page_size, total=total)


@router.get(
    "/{project_id}/keyframes/export",
    response_class=StreamingResponse,
    summary="批量导出项目关键帧 ZIP",
)
async def export_project_keyframes(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    _project: Project = Depends(require_project_owner),
) -> StreamingResponse:
    """导出已有关键帧，ZIP 内按章节、镜头序号和镜头标题命名。"""
    filename, items = await list_project_keyframe_export_items(db, project_id=project_id)
    return StreamingResponse(
        iter_keyframe_archive(items),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "X-Shotcat-Keyframe-Count": str(len(items)),
        },
    )


@router.get(
    "/{project_id}/assets/export",
    response_class=StreamingResponse,
    summary="批量导出项目设定图 ZIP",
)
async def export_project_assets(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    _project: Project = Depends(require_project_owner),
) -> StreamingResponse:
    """导出角色、场景、道具的全部已生成设定图，按类型目录和资产名称命名。"""
    filename, items = await list_project_asset_export_items(db, project_id=project_id)
    return StreamingResponse(
        iter_file_archive(items, skipped_label="设定图"),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "X-Shotcat-Asset-Count": str(len(items)),
        },
    )


@router.post(
    "",
    response_model=ApiResponse[ProjectRead],
    status_code=status.HTTP_201_CREATED,
    summary="创建项目",
)
async def create_project(
    body: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[ProjectRead]:
    await ensure_not_exists(
        db,
        Project,
        body.id,
        detail=entity_already_exists("Project"),
    )
    try:
        _validate_project_style_combo(visual_style=body.visual_style, style=body.style)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    # owner_id 只能来自登录态，不能从请求体读，防止伪造归属
    obj = await create_and_refresh(db, Project(**body.model_dump(), owner_id=current_user.id))
    return created_response(ProjectRead.model_validate(obj))


@router.get(
    "/{project_id}",
    response_model=ApiResponse[ProjectRead],
    summary="获取项目",
)
async def get_project(
    project_id: str,
    obj: Project = Depends(require_project_owner),
) -> ApiResponse[ProjectRead]:
    return success_response(ProjectRead.model_validate(obj))


@router.patch(
    "/{project_id}",
    response_model=ApiResponse[ProjectRead],
    summary="更新项目",
)
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    obj: Project = Depends(require_project_owner),
) -> ApiResponse[ProjectRead]:
    update_data = body.model_dump(exclude_unset=True)
    visual_style = update_data.get("visual_style", obj.visual_style)
    style = update_data.get("style", obj.style)
    if visual_style is not None and style is not None:
        try:
            _validate_project_style_combo(visual_style=visual_style, style=style)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    patch_model(obj, update_data)
    await flush_and_refresh(db, obj)
    return success_response(ProjectRead.model_validate(obj))


@router.delete(
    "/{project_id}",
    response_model=ApiResponse[None],
    summary="删除项目",
)
async def delete_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    obj: Project = Depends(require_project_owner),
) -> ApiResponse[None]:
    await db.delete(obj)
    await db.flush()
    return empty_response()
