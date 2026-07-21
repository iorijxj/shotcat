"""公共资产清理回归测试（多租户 M2 P3-B）。

覆盖：删 project_id 为空的四类资产并靠外键级联清子表；SET NULL 副作用生效；
私有资产不受影响；幂等；RESTRICT 阻塞（公共 actor 仍被 character 引用）时
报告并中止、不删任何行。

用 StaticPool + PRAGMA foreign_keys=ON，保证内存库单连接内外键级联真实生效
（普通内存 engine 不带 app 的外键 PRAGMA）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.cli.purge_public_assets import PublicActorStillReferencedError, purge_public_assets
from app.core.db import Base
from app.models.auth import User
from tests.conftest import assets_project_id_nullable, root_tenant_id_nullable
from app.models.studio import (
    Actor,
    Chapter,
    Character,
    CharacterPropLink,
    Costume,
    Project,
    ProjectSceneLink,
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

_STYLE = ProjectStyle.real_people_city


async def _build_session() -> tuple[AsyncSession, object]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _rec):  # noqa: ANN001, ANN202
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    session_local = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    with assets_project_id_nullable(), root_tenant_id_nullable():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    return session_local(), engine


async def _count(db: AsyncSession, model: type) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


@pytest.mark.asyncio
async def test_purge_cascades_children_and_set_null() -> None:
    db, engine = await _build_session()
    async with db:
        db.add(User(id="U", username="u", password_hash="x"))
        db.add(Project(id="P1", name="p1", style=_STYLE, owner_id="U"))
        db.add(Chapter(id="CH1", project_id="P1", index=1, title="第一章", status=ChapterStatus.draft))
        db.add(Shot(id="SH1", chapter_id="CH1", index=1, title="镜头一", status=ShotStatus.pending))
        # 公共 scene + 图片 + link + shot_detail 引用（SET NULL）
        db.add(Scene(id="S_pub", name="s_pub", style=_STYLE, project_id=None))
        db.add(SceneImage(scene_id="S_pub"))
        db.add(ProjectSceneLink(project_id="P1", scene_id="S_pub"))
        db.add(
            ShotDetail(
                id="SH1",
                camera_shot=CameraShotType.ms,
                angle=CameraAngle.eye_level,
                movement=CameraMovement.static,
                scene_id="S_pub",
            )
        )
        await db.commit()

        result = await purge_public_assets(db)
        await db.commit()

        assert result.scenes == 1
        assert await _count(db, Scene) == 0
        assert await _count(db, SceneImage) == 0  # ON DELETE CASCADE
        assert await _count(db, ProjectSceneLink) == 0  # ON DELETE CASCADE
        assert (await db.get(ShotDetail, "SH1")).scene_id is None  # ON DELETE SET NULL
    await engine.dispose()


@pytest.mark.asyncio
async def test_purge_leaves_private_assets_untouched() -> None:
    db, engine = await _build_session()
    async with db:
        db.add(User(id="U", username="u", password_hash="x"))
        db.add(Project(id="P1", name="p1", style=_STYLE, owner_id="U"))
        await db.commit()
        db.add(Scene(id="S_priv", name="s_priv", style=_STYLE, project_id="P1"))
        db.add(Prop(id="Pr_pub", name="pr", style=_STYLE, project_id=None))
        await db.commit()

        result = await purge_public_assets(db)
        await db.commit()

        assert result.props == 1
        assert await _count(db, Prop) == 0
        assert await _count(db, Scene) == 1  # 私有 scene 保留
    await engine.dispose()


@pytest.mark.asyncio
async def test_purge_is_idempotent() -> None:
    db, engine = await _build_session()
    async with db:
        db.add(Actor(id="A_pub", name="a", style=_STYLE, project_id=None))
        await db.commit()

        first = await purge_public_assets(db)
        await db.commit()
        assert first.actors == 1

        second = await purge_public_assets(db)
        await db.commit()
        assert (second.scenes, second.props, second.costumes, second.actors) == (0, 0, 0, 0)
    await engine.dispose()


@pytest.mark.asyncio
async def test_purge_aborts_when_public_actor_still_referenced() -> None:
    db, engine = await _build_session()
    async with db:
        db.add(User(id="U", username="u", password_hash="x"))
        db.add(Project(id="P1", name="p1", style=_STYLE, owner_id="U"))
        db.add(Actor(id="A_pub", name="a", style=_STYLE, project_id=None))
        db.add(Character(id="C1", project_id="P1", name="c1", style=_STYLE, actor_id="A_pub"))
        db.add(Costume(id="Co_pub", name="co", style=_STYLE, project_id=None))
        await db.commit()

        with pytest.raises(PublicActorStillReferencedError, match="C1"):
            await purge_public_assets(db)
        await db.rollback()

        # 中止后不删任何行（含其他可删资产）
        assert await _count(db, Actor) == 1
        assert await _count(db, Costume) == 1
    await engine.dispose()
