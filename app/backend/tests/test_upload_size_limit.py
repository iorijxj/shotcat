"""文件上传大小限制回归测试（安全整改阶段三 3.2）：图片/视频按类型限制，超限 413。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.core.db import Base
from app.services.studio import files as files_service

_MB = 1024 * 1024


async def _build_session() -> tuple[AsyncSession, object]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    session_local = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return session_local(), engine


def _upload_file_of(filename: str, size_bytes: int) -> UploadFile:
    import io

    return UploadFile(file=io.BytesIO(b"x" * size_bytes), filename=filename)


@pytest.mark.asyncio
async def test_oversized_image_rejected_with_413() -> None:
    db, engine = await _build_session()
    async with db:
        too_big = settings.upload_max_image_mb * _MB + 1
        with pytest.raises(HTTPException) as exc_info:
            await files_service.upload_file(db, file=_upload_file_of("a.png", too_big))
        assert exc_info.value.status_code == 413
    await engine.dispose()


@pytest.mark.asyncio
async def test_oversized_video_rejected_with_413() -> None:
    db, engine = await _build_session()
    async with db:
        too_big = settings.upload_max_video_mb * _MB + 1
        with pytest.raises(HTTPException) as exc_info:
            await files_service.upload_file(db, file=_upload_file_of("b.mp4", too_big))
        assert exc_info.value.status_code == 413
    await engine.dispose()


@pytest.mark.asyncio
async def test_image_at_limit_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_storage_upload(**kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(url="http://storage/fake")

    monkeypatch.setattr(files_service.storage, "upload_file", _fake_storage_upload)

    db, engine = await _build_session()
    async with db:
        at_limit = settings.upload_max_image_mb * _MB
        item = await files_service.upload_file(db, file=_upload_file_of("ok.png", at_limit))
        assert item.id
        assert item.thumbnail == "http://storage/fake"
    await engine.dispose()
