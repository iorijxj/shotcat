"""清理历史公共资产（多租户 M2 P3-B，不可逆删除）。

删除 project_id 为空的 scene/prop/costume/actor 四类历史全局资产。级联从属
（各 *_images、project_*_links、character_prop_links）靠数据库外键 ON DELETE
CASCADE 一并删除；shot_details.scene_id / characters.costume_id 会被 SET NULL。

⚠️ 删除不可逆：跑本脚本前务必先跑 export_public_assets 导出备份。

characters.actor_id 是 ON DELETE RESTRICT：若仍有 character（真实项目下）引用
某公共 actor，删除会被数据库挡住。本脚本删除前先做预检，检测到这类引用就打印
清单并中止（交人工决策），绝不强删破坏真实项目数据。

幂等，可重复执行（无 project_id 为空的资产时删 0 行）。用法：
    uv run python -m app.cli.purge_public_assets
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_maker
from app.models.studio import Actor, Character, Costume, Prop, Scene

_ASSET_MODELS = (Scene, Prop, Costume, Actor)


class PublicActorStillReferencedError(RuntimeError):
    """公共 actor 仍被真实项目的 character 引用（RESTRICT），删除会被挡，中止清理。"""


@dataclass(frozen=True)
class PurgeResult:
    scenes: int
    props: int
    costumes: int
    actors: int


async def _blocking_actor_refs(db: AsyncSession) -> list[tuple[str, str]]:
    """返回 [(character_id, actor_id)]：引用了公共 actor 的 character。"""
    public_actor_ids = select(Actor.id).where(Actor.project_id.is_(None))
    rows = await db.execute(
        select(Character.id, Character.actor_id).where(Character.actor_id.in_(public_actor_ids))
    )
    return [(r.id, r.actor_id) for r in rows]


async def purge_public_assets(db: AsyncSession) -> PurgeResult:
    """删除四类 project_id 为空的资产；调用方负责 commit。

    删除前预检 characters.actor_id RESTRICT 阻塞，命中则抛
    PublicActorStillReferencedError 中止（不删任何行）。
    """
    blockers = await _blocking_actor_refs(db)
    if blockers:
        detail = "、".join(f"character={cid} → actor={aid}" for cid, aid in blockers)
        raise PublicActorStillReferencedError(
            f"以下 character 仍引用公共 actor（RESTRICT），无法删除，请人工处理后重试：{detail}"
        )

    counts: dict[str, int] = {}
    for model in _ASSET_MODELS:
        result = await db.execute(delete(model).where(model.project_id.is_(None)))
        counts[model.__tablename__] = result.rowcount or 0

    return PurgeResult(
        scenes=counts["scenes"],
        props=counts["props"],
        costumes=counts["costumes"],
        actors=counts["actors"],
    )


async def _run() -> None:
    async with async_session_maker() as db:
        result = await purge_public_assets(db)
        await db.commit()
    print(
        "公共资产清理完成："
        f"scenes={result.scenes} props={result.props} costumes={result.costumes} actors={result.actors}"
    )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
