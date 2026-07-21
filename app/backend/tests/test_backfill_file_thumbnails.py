"""存量 FileItem.thumbnail 回填回归测试（M4 对象存储访问加固 M4-2）。

覆盖：bucket 公开 URL 被重写为代理下载 URL、已是代理 URL 的跳过、重复执行幂等、
空 thumbnail 跳过不产生指向空文件的下载链接。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.cli.backfill_file_thumbnails import backfill_file_thumbnails
from app.core.db import Base
from app.models.studio import FileItem, FileType
from app.services.studio.entity_thumbnails import download_url


async def _build_session() -> tuple[AsyncSession, object]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    session_local = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return session_local(), engine


@pytest.mark.asyncio
async def test_backfill_rewrites_bucket_url_to_proxy_url() -> None:
    db, engine = await _build_session()
    async with db:
        db.add(
            FileItem(
                id="f1",
                type=FileType.image,
                name="旧图",
                thumbnail="https://bucket.example.com/files/a.png",
                tags=[],
                storage_key="files/a.png",
            )
        )
        await db.commit()

        result = await backfill_file_thumbnails(db)
        await db.commit()

        assert result.rewritten == 1
        assert result.skipped == 0
        item = await db.get(FileItem, "f1")
        assert item is not None
        assert item.thumbnail == download_url("f1")
    await engine.dispose()


@pytest.mark.asyncio
async def test_backfill_skips_already_proxy_url() -> None:
    db, engine = await _build_session()
    async with db:
        db.add(
            FileItem(
                id="f1",
                type=FileType.image,
                name="新图",
                thumbnail=download_url("f1"),
                tags=[],
                storage_key="files/x.png",
            )
        )
        await db.commit()

        result = await backfill_file_thumbnails(db)
        await db.commit()

        assert result.rewritten == 0
        assert result.skipped == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_backfill_skips_empty_thumbnail() -> None:
    db, engine = await _build_session()
    async with db:
        db.add(
            FileItem(
                id="f1",
                type=FileType.image,
                name="无缩略图",
                thumbnail="",
                tags=[],
                storage_key="files/x.png",
            )
        )
        await db.commit()

        result = await backfill_file_thumbnails(db)
        await db.commit()

        assert result.rewritten == 0
        assert result.skipped == 1
        item = await db.get(FileItem, "f1")
        assert item is not None
        assert item.thumbnail == ""
    await engine.dispose()


@pytest.mark.asyncio
async def test_backfill_is_idempotent() -> None:
    db, engine = await _build_session()
    async with db:
        db.add(
            FileItem(
                id="f1",
                type=FileType.image,
                name="旧图",
                thumbnail="https://bucket.example.com/files/a.png",
                tags=[],
                storage_key="files/a.png",
            )
        )
        await db.commit()

        first = await backfill_file_thumbnails(db)
        await db.commit()
        assert first.rewritten == 1

        second = await backfill_file_thumbnails(db)
        await db.commit()
        assert second.rewritten == 0
        assert second.skipped == 1
    await engine.dispose()
