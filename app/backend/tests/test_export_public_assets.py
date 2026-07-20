"""公共资产导出备份回归测试（多租户 M2 P3-A）。

覆盖：只导出 project_id 为空的四类资产及其级联从属；私有资产不入档；
记录 SET NULL 副作用（shots.scene_id / characters.costume_id）与 RESTRICT
阻塞点（characters.actor_id 仍引用公共 actor）。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.cli.export_public_assets import collect_public_assets, summarize
from app.core.db import Base
from tests.conftest import assets_project_id_nullable
from app.models.studio import (
    Actor,
    Chapter,
    Character,
    CharacterPropLink,
    Costume,
    Project,
    Prop,
    Scene,
    SceneImage,
    Shot,
    ShotDetail,
)
from app.models.types import (
    CameraAngle,
    CameraMovement,
    CameraShotType,
    ChapterStatus,
    ProjectStyle,
    ShotStatus,
)


async def _build_session() -> tuple[AsyncSession, object]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    session_local = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    with assets_project_id_nullable():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    return session_local(), engine


async def _seed(db: AsyncSession) -> None:
    style = ProjectStyle.real_people_city
    db.add(Project(id="P1", name="p1", style=style, owner_id="U"))
    # 公共资产（project_id 为空）——应被导出
    db.add(Scene(id="S_pub", name="s_pub", style=style, project_id=None))
    db.add(SceneImage(scene_id="S_pub"))
    db.add(Prop(id="Pr_pub", name="pr_pub", style=style, project_id=None))
    db.add(Costume(id="Co_pub", name="co_pub", style=style, project_id=None))
    db.add(Actor(id="A_pub", name="a_pub", style=style, project_id=None))
    # 私有资产（有 project_id）——不应被导出
    db.add(Scene(id="S_priv", name="s_priv", style=style, project_id="P1"))
    # 真实项目下的 shot_detail.scene_id 引用公共 scene（删除时 SET NULL）
    db.add(Chapter(id="CH1", project_id="P1", index=1, title="第一章", status=ChapterStatus.draft))
    db.add(Shot(id="SH1", chapter_id="CH1", index=1, title="镜头一", status=ShotStatus.pending))
    db.add(
        ShotDetail(
            id="SH1",
            camera_shot=CameraShotType.ms,
            angle=CameraAngle.eye_level,
            movement=CameraMovement.static,
            scene_id="S_pub",
        )
    )
    # 角色引用公共 actor（RESTRICT 阻塞）+ 公共 costume（SET NULL）
    db.add(
        Character(
            id="C1",
            project_id="P1",
            name="c1",
            style=style,
            actor_id="A_pub",
            costume_id="Co_pub",
        )
    )
    db.add(CharacterPropLink(character_id="C1", prop_id="Pr_pub", index=0))
    await db.flush()


@pytest.mark.asyncio
async def test_export_only_public_assets_and_children() -> None:
    db, engine = await _build_session()
    async with db:
        await _seed(db)
        bundle = await collect_public_assets(db)
    await engine.dispose()

    assert [r["id"] for r in bundle["assets"]["scenes"]] == ["S_pub"]  # 私有 S_priv 不入档
    assert [r["id"] for r in bundle["assets"]["props"]] == ["Pr_pub"]
    assert [r["id"] for r in bundle["assets"]["costumes"]] == ["Co_pub"]
    assert [r["id"] for r in bundle["assets"]["actors"]] == ["A_pub"]

    assert len(bundle["children"]["scene_images"]) == 1
    assert len(bundle["children"]["character_prop_links"]) == 1


@pytest.mark.asyncio
async def test_export_records_side_effects_and_blockers() -> None:
    db, engine = await _build_session()
    async with db:
        await _seed(db)
        bundle = await collect_public_assets(db)
    await engine.dispose()

    se = bundle["side_effects"]
    assert se["shot_details_scene_id_set_null"] == [{"id": "SH1", "scene_id": "S_pub"}]
    assert se["characters_costume_id_set_null"] == [{"id": "C1", "costume_id": "Co_pub"}]
    assert se["characters_actor_id_restrict"] == [{"id": "C1", "actor_id": "A_pub"}]
    assert "RESTRICT" in summarize(bundle)


@pytest.mark.asyncio
async def test_export_empty_when_no_public_assets() -> None:
    db, engine = await _build_session()
    async with db:
        db.add(Project(id="P1", name="p1", style=ProjectStyle.real_people_city, owner_id="U"))
        db.add(Scene(id="S_priv", name="s_priv", style=ProjectStyle.real_people_city, project_id="P1"))
        await db.flush()
        bundle = await collect_public_assets(db)
    await engine.dispose()

    assert all(rows == [] for rows in bundle["assets"].values())
    assert bundle["side_effects"]["characters_actor_id_restrict"] == []
