"""幂等回填：把存量 FileItem.thumbnail 从对象存储公开 URL 重写为后端代理下载 URL
（M4 对象存储访问加固 M4-2）。

背景：M4-1 之前上传的 FileItem.thumbnail 存的是 bucket 公开 URL（storage._build_public_url
生成），bucket 私有化（M4-3）后该 URL 会 403。本脚本把这些行重写为
/api/v1/studio/files/{id}/download，与新上传行为（M4-1）及实体缩略图口径一致。

前置：需先部署 M4-1（新上传已是代理 URL），再跑本脚本回填存量，最后才由运维执行
M4-3 关闭 bucket 匿名读——顺序不可颠倒，否则回填前私有化会导致旧图挂。

用法：
    uv run python -m app.cli.backfill_file_thumbnails

只处理 thumbnail 尚未是代理 URL 且非空的行，已是代理 URL 或为空的跳过，可重复执行，幂等。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_maker
from app.models.studio import FileItem
from app.services.studio.entity_thumbnails import download_url


@dataclass(frozen=True)
class BackfillResult:
    """回填统计：重写数 / 跳过数（已是代理 URL 或为空）。"""

    rewritten: int
    skipped: int


async def backfill_file_thumbnails(db: AsyncSession) -> BackfillResult:
    """把非代理 URL 的 FileItem.thumbnail 重写为代理下载 URL，返回统计；调用方负责 commit。"""
    items = list((await db.execute(select(FileItem))).scalars().all())
    rewritten = skipped = 0
    for item in items:
        target = download_url(item.id)
        if not item.thumbnail or item.thumbnail == target:
            skipped += 1
            continue
        item.thumbnail = target
        rewritten += 1
    await db.flush()
    return BackfillResult(rewritten=rewritten, skipped=skipped)


async def _run() -> None:
    async with async_session_maker() as db:
        result = await backfill_file_thumbnails(db)
        await db.commit()
        print(
            f"thumbnail 回填完成：重写 {result.rewritten} 个，"
            f"跳过 {result.skipped} 个（已是代理 URL 或为空）"
        )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
