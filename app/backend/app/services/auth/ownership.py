"""项目所有权校验：给各路由复用的辅助函数，统一"不存在或不属于当前用户 -> 404"的判断。

- 直接挂 project_id 的端点：用 `require_project_owner` 作为 FastAPI 依赖。
- 经 chapter/shot 间接挂靠的端点：`require_project_access_via_chapter` / `_via_shot`。
- 可空 project_id 的四类历史全局资产（scene/prop/costume/actor）：`assert_entity_owned`，
  project_id 为空视为公共资产，登录用户均可访问；Character（project_id 非空）必须校验归属。
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db
from app.models.auth import User
from app.models.studio import Actor, Chapter, Character, Costume, FileUsage, Project, Prop, Scene, Shot
from app.services.common import entity_not_found

ENTITY_MODEL_BY_TYPE: dict[str, type] = {
    "character": Character,
    "scene": Scene,
    "prop": Prop,
    "costume": Costume,
    "actor": Actor,
}

_ENTITY_DISPLAY_NAME_BY_TYPE: dict[str, str] = {
    "character": "Character",
    "scene": "Scene",
    "prop": "Prop",
    "costume": "Costume",
    "actor": "Actor",
}


def _not_owned(project: Project | None, current_user: User) -> bool:
    return project is None or project.owner_id != current_user.id


async def assert_project_owned(
    db: AsyncSession,
    *,
    project_id: str,
    current_user: User,
    not_found_name: str = "Project",
) -> Project:
    """校验 project_id 归属当前用户；不存在或不属于当前用户一律 404（不用 403，避免暴露资源是否存在）。"""
    project = await db.get(Project, project_id)
    if _not_owned(project, current_user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=entity_not_found(not_found_name))
    return project


async def require_project_owner(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Project:
    """FastAPI 依赖：path 里直接带 project_id 的端点用这个。"""
    return await assert_project_owned(db, project_id=project_id, current_user=current_user)


async def assert_chapter_owned(db: AsyncSession, *, chapter_id: str, current_user: User) -> Chapter:
    chapter = await db.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=entity_not_found("Chapter"))
    await assert_project_owned(db, project_id=chapter.project_id, current_user=current_user, not_found_name="Chapter")
    return chapter


async def require_project_access_via_chapter(
    chapter_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Chapter:
    return await assert_chapter_owned(db, chapter_id=chapter_id, current_user=current_user)


async def assert_shot_owned(db: AsyncSession, *, shot_id: str, current_user: User) -> Shot:
    shot = await db.get(Shot, shot_id)
    if shot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=entity_not_found("Shot"))
    await assert_chapter_owned(db, chapter_id=shot.chapter_id, current_user=current_user)
    return shot


async def require_project_access_via_shot(
    shot_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Shot:
    return await assert_shot_owned(db, shot_id=shot_id, current_user=current_user)


async def assert_entity_owned(db: AsyncSession, *, entity_type: str, entity_id: str, current_user: User) -> object:
    """entity_type 为 character/scene/prop/costume/actor 之一；project_id 为空视为公共资产放行。"""
    model = ENTITY_MODEL_BY_TYPE.get(entity_type)
    if model is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"unsupported entity_type: {entity_type}")
    display_name = _ENTITY_DISPLAY_NAME_BY_TYPE[entity_type]
    obj = await db.get(model, entity_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=entity_not_found(display_name))
    if obj.project_id is not None:
        await assert_project_owned(db, project_id=obj.project_id, current_user=current_user, not_found_name=display_name)
    return obj


async def assert_file_owned(db: AsyncSession, *, file_id: str, current_user: User) -> None:
    """FileItem 本身无归属，归属信息在 file_usages（同一文件可能挂多条、跨项目）。
    没有任何关联记录时视为公共（如刚上传、尚未落库使用记录的瞬时状态）；
    有关联记录时必须至少命中一个当前用户拥有的项目。"""
    stmt = select(FileUsage.project_id).where(FileUsage.file_id == file_id).distinct()
    project_ids = [row[0] for row in (await db.execute(stmt)).all()]
    if not project_ids:
        return
    owned_stmt = select(Project.id).where(Project.id.in_(project_ids), Project.owner_id == current_user.id)
    owned = (await db.execute(owned_stmt)).first()
    if owned is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=entity_not_found("File"))
