"""Chapter CRUD（从 projects.py 拆分）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.utils import apply_keyword_filter, apply_order, paginate
from app.dependencies import get_current_tenant, get_current_user, get_db
from app.models.auth import User
from app.models.studio import Chapter, Project, Shot
from app.services.auth.tenants import TenantContext
from app.schemas.common import ApiResponse, PaginatedData, created_response, empty_response, paginated_response, success_response
from app.services.auth.ownership import assert_project_owned, require_project_access_via_chapter
from app.services.common import (
    create_and_refresh,
    entity_already_exists,
    ensure_not_exists,
    flush_and_refresh,
    patch_model,
)
from app.schemas.studio.projects import ChapterCreate, ChapterRead, ChapterUpdate

router = APIRouter()

CHAPTER_ORDER_FIELDS = {"index", "title", "created_at", "updated_at", "storyboard_count", "status"}


@router.get(
    "",
    response_model=ApiResponse[PaginatedData[ChapterRead]],
    summary="章节列表（分页）",
)
async def list_chapters(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    tenant: TenantContext = Depends(get_current_tenant),
    project_id: str | None = Query(None, description="按项目过滤"),
    q: str | None = Query(None, description="关键字，过滤 title/summary"),
    order: str | None = Query(None, description="排序字段"),
    is_desc: bool = Query(False, description="是否倒序"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
) -> ApiResponse[PaginatedData[ChapterRead]]:
    stmt = select(Chapter)
    if project_id:
        await assert_project_owned(db, project_id=project_id, current_user=current_user)
        stmt = stmt.where(Chapter.project_id == project_id)
    else:
        owned_project_ids = select(Project.id).where(Project.tenant_id == tenant.tenant_id)
        stmt = stmt.where(Chapter.project_id.in_(owned_project_ids))
    stmt = apply_keyword_filter(stmt, q=q, fields=[Chapter.title, Chapter.summary])
    stmt = apply_order(
        stmt,
        model=Chapter,
        order=order,
        is_desc=is_desc,
        allow_fields=CHAPTER_ORDER_FIELDS,
        default="index",
    )
    items, total = await paginate(db, stmt=stmt, page=page, page_size=page_size)

    chapter_ids = [c.id for c in items]
    shot_count_by_chapter: dict[str, int] = {}
    if chapter_ids:
        count_stmt = (
            select(Shot.chapter_id, func.count(Shot.id))
            .where(Shot.chapter_id.in_(chapter_ids))
            .group_by(Shot.chapter_id)
        )
        res = await db.execute(count_stmt)
        shot_count_by_chapter = {str(ch_id): int(cnt) for ch_id, cnt in res.all()}

    return paginated_response(
        [
            ChapterRead.model_validate(x).model_copy(update={"shot_count": shot_count_by_chapter.get(x.id, 0)})
            for x in items
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post(
    "",
    response_model=ApiResponse[ChapterRead],
    status_code=status.HTTP_201_CREATED,
    summary="创建章节",
)
async def create_chapter(
    body: ChapterCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[ChapterRead]:
    await ensure_not_exists(
        db,
        Chapter,
        body.id,
        detail=entity_already_exists("Chapter"),
    )
    await assert_project_owned(db, project_id=body.project_id, current_user=current_user)
    obj = await create_and_refresh(db, Chapter(**body.model_dump()))
    return created_response(ChapterRead.model_validate(obj))


@router.get(
    "/{chapter_id}",
    response_model=ApiResponse[ChapterRead],
    summary="获取章节",
)
async def get_chapter(
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    obj: Chapter = Depends(require_project_access_via_chapter),
) -> ApiResponse[ChapterRead]:
    count_stmt = select(func.count(Shot.id)).where(Shot.chapter_id == chapter_id)
    res = await db.execute(count_stmt)
    shot_count = int(res.scalar() or 0)
    return success_response(ChapterRead.model_validate(obj).model_copy(update={"shot_count": shot_count}))


@router.patch(
    "/{chapter_id}",
    response_model=ApiResponse[ChapterRead],
    summary="更新章节",
)
async def update_chapter(
    chapter_id: str,
    body: ChapterUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    obj: Chapter = Depends(require_project_access_via_chapter),
) -> ApiResponse[ChapterRead]:
    update = body.model_dump(exclude_unset=True)
    if "project_id" in update:
        await assert_project_owned(db, project_id=update["project_id"], current_user=current_user)
    patch_model(obj, update)
    await flush_and_refresh(db, obj)
    return success_response(ChapterRead.model_validate(obj))


@router.delete(
    "/{chapter_id}",
    response_model=ApiResponse[None],
    summary="删除章节",
)
async def delete_chapter(
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    obj: Chapter = Depends(require_project_access_via_chapter),
) -> ApiResponse[None]:
    await db.delete(obj)
    await db.flush()
    return empty_response()
