"""四类资产 project_id NOT NULL 回归测试（多租户 M2 P3-D）。

覆盖：
- 模型层：create_all 后创建不带 project_id 的资产被 NOT NULL 约束拒绝。
- 迁移 CLI：仍有 project_id 为空残留时中止（提示先 purge）；无残留则放行；幂等。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.cli.ensure_asset_project_not_null import (
    AssetProjectNullError,
    ensure_asset_project_not_null,
)
from app.core.db import Base
from app.models.studio import Scene
from app.models.types import ProjectStyle


@pytest.mark.asyncio
async def test_create_asset_without_project_id_is_rejected() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with maker() as db:
        db.add(Scene(id="S1", name="s", style=ProjectStyle.real_people_city, project_id=None))
        with pytest.raises(IntegrityError):
            await db.commit()
    await engine.dispose()


def _make_legacy_scenes_table_with_null() -> object:
    """建一张 project_id 可空的旧 scenes 表并塞一行 NULL，模拟迁移前的存量库。"""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        for table in ("scenes", "props", "costumes", "actors"):
            conn.execute(text(f"CREATE TABLE {table} (id VARCHAR(64) PRIMARY KEY, project_id VARCHAR(64) NULL)"))
        conn.execute(text("INSERT INTO scenes (id, project_id) VALUES ('S_null', NULL)"))
    return engine


def test_migration_aborts_when_null_project_remains() -> None:
    engine = _make_legacy_scenes_table_with_null()
    with engine.begin() as conn:
        with pytest.raises(AssetProjectNullError, match="scenes=1"):
            ensure_asset_project_not_null(conn)
    engine.dispose()


def test_migration_passes_and_is_idempotent_without_null() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        for table in ("scenes", "props", "costumes", "actors"):
            conn.execute(text(f"CREATE TABLE {table} (id VARCHAR(64) PRIMARY KEY, project_id VARCHAR(64) NOT NULL)"))
    with engine.begin() as conn:
        # 非 MySQL 方言只做残留校验、不 ALTER，返回空列表；无残留不抛错
        assert ensure_asset_project_not_null(conn) == []
    with engine.begin() as conn:  # 幂等
        assert ensure_asset_project_not_null(conn) == []
    engine.dispose()
