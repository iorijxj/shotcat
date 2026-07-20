"""GenerationTaskLink 任务链接系统的多态归属校验（阶段四 4.1）。

任务/任务关联按 `relation_type` 多态引用不同业务实体，没有统一外键。这里按 relation_type
分支反查到 Project.owner_id，复用 `ownership.py` 既有的 assert_* 助手（不另起炉灶）：

- chapter 系（relation_entity_id 直接是 chapter_id）：assert_chapter_owned
- chapter-or-project 系（创建时 pick_* 优先 chapter、回落 project）：先探 chapter 再探 project
- shot 系（relation_entity_id 直接是 shot_id）：assert_shot_owned
- shot_frame_image：relation_entity_id 是 ShotFrameImage.id → shot_detail_id(与 shots.id 共享主键)
- *_image：relation_entity_id 是各图片表主键(int) → 反查外键实体 → assert_entity_owned
  （scene/prop/costume/actor 的 project_id 可空视为公共资产放行，character 强制归属）

未知 relation_type 或反查不到实体时一律 fail-closed（404），不暴露关联实体类型。
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import User
from app.models.studio import (
    ActorImage,
    Chapter,
    CharacterImage,
    CostumeImage,
    Project,
    PropImage,
    SceneImage,
    ShotFrameImage,
)
from app.models.task_links import GenerationTaskLink
from app.services.auth.ownership import (
    assert_chapter_owned,
    assert_entity_owned,
    assert_project_owned,
    assert_shot_owned,
)
from app.services.common import entity_not_found

# relation_entity_id 直接是 chapter_id
_CHAPTER_RELATION_TYPES = {"chapter_division", "script_extraction"}
# relation_entity_id 是 chapter_id 或 project_id（创建时 pick_*_relation_entity_id 优先 chapter、回落 project）
_CHAPTER_OR_PROJECT_RELATION_TYPES = {
    "entity_merge",
    "consistency_check",
    "variant_analysis",
    "character_portrait_analysis",
    "prop_info_analysis",
    "scene_info_analysis",
    "costume_info_analysis",
    "script_optimization",
    "script_simplification",
}
# relation_entity_id 直接是 shot_id
_SHOT_RELATION_TYPES = {
    "video",
    "shot_first_frame_prompt",
    "shot_last_frame_prompt",
    "shot_key_frame_prompt",
}
# relation_entity_id 是各图片表主键(int)：relation_type -> (entity_type, 图片模型, 指向实体的外键字段)
_IMAGE_RELATION_SPECS: dict[str, tuple[str, type, str]] = {
    "actor_image": ("actor", ActorImage, "actor_id"),
    "scene_image": ("scene", SceneImage, "scene_id"),
    "prop_image": ("prop", PropImage, "prop_id"),
    "costume_image": ("costume", CostumeImage, "costume_id"),
    "character_image": ("character", CharacterImage, "character_id"),
}


def _task_not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=entity_not_found("Task"))


async def _assert_image_link_owned(
    db: AsyncSession,
    *,
    entity_type: str,
    image_model: type,
    fk_field: str,
    relation_entity_id: str,
    current_user: User,
) -> None:
    try:
        image_id = int(relation_entity_id)
    except (TypeError, ValueError):
        raise _task_not_found()
    image = await db.get(image_model, image_id)
    if image is None:
        raise _task_not_found()
    await assert_entity_owned(
        db, entity_type=entity_type, entity_id=getattr(image, fk_field), current_user=current_user
    )


async def _resolve_and_assert(db: AsyncSession, *, link: GenerationTaskLink, current_user: User) -> None:
    relation_type = str(link.relation_type or "")
    relation_entity_id = str(link.relation_entity_id or "")
    if not relation_entity_id:
        raise _task_not_found()

    if relation_type in _CHAPTER_RELATION_TYPES:
        await assert_chapter_owned(db, chapter_id=relation_entity_id, current_user=current_user)
        return
    if relation_type in _CHAPTER_OR_PROJECT_RELATION_TYPES:
        if await db.get(Chapter, relation_entity_id) is not None:
            await assert_chapter_owned(db, chapter_id=relation_entity_id, current_user=current_user)
            return
        if await db.get(Project, relation_entity_id) is not None:
            await assert_project_owned(db, project_id=relation_entity_id, current_user=current_user)
            return
        raise _task_not_found()
    if relation_type in _SHOT_RELATION_TYPES:
        await assert_shot_owned(db, shot_id=relation_entity_id, current_user=current_user)
        return
    if relation_type == "shot_frame_image":
        try:
            frame_id = int(relation_entity_id)
        except (TypeError, ValueError):
            raise _task_not_found()
        frame = await db.get(ShotFrameImage, frame_id)
        if frame is None:
            raise _task_not_found()
        await assert_shot_owned(db, shot_id=frame.shot_detail_id, current_user=current_user)
        return
    spec = _IMAGE_RELATION_SPECS.get(relation_type)
    if spec is not None:
        entity_type, image_model, fk_field = spec
        await _assert_image_link_owned(
            db,
            entity_type=entity_type,
            image_model=image_model,
            fk_field=fk_field,
            relation_entity_id=relation_entity_id,
            current_user=current_user,
        )
        return
    # 未知 relation_type：无法解析归属，安全起见拒绝（fail-closed）
    raise _task_not_found()


async def assert_task_link_owned(db: AsyncSession, *, link: GenerationTaskLink, current_user: User) -> None:
    """校验单条 GenerationTaskLink 归属当前用户；越权或反查不到一律 404（统一文案，不泄露实体类型）。"""
    try:
        await _resolve_and_assert(db, link=link, current_user=current_user)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise _task_not_found() from exc
        raise


async def is_task_link_accessible(db: AsyncSession, *, link: GenerationTaskLink, current_user: User) -> bool:
    """不抛异常版：供列表接口页内过滤用。"""
    try:
        await assert_task_link_owned(db, link=link, current_user=current_user)
        return True
    except HTTPException:
        return False


async def assert_task_owned(db: AsyncSession, *, task_id: str, current_user: User) -> None:
    """经任务的关联记录反查归属：任一关联归属当前用户即放行；无任何关联则 fail-closed 404。"""
    links = list(
        (await db.execute(select(GenerationTaskLink).where(GenerationTaskLink.task_id == task_id))).scalars().all()
    )
    if not links:
        raise _task_not_found()
    for link in links:
        if await is_task_link_accessible(db, link=link, current_user=current_user):
            return
    raise _task_not_found()


async def is_task_accessible(db: AsyncSession, *, task_id: str, current_user: User) -> bool:
    """不抛异常版：供任务列表接口页内过滤用。"""
    try:
        await assert_task_owned(db, task_id=task_id, current_user=current_user)
        return True
    except HTTPException:
        return False
