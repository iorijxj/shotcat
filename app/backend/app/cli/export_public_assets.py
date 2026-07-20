"""导出将被清理的历史公共资产（多租户 M2 P3，删除前的可审查备份）。

P3 要删除 project_id 为空的 scene/prop/costume/actor 四类历史全局资产。删除
不可逆，故先把这些行 + 其级联从属（各 *_images、project_*_links、
character_prop_links）整理成带时间戳的 JSON 落地，删前必有存档、可选择性回填。

同时记录删除的 SET NULL 副作用（shots.scene_id / characters.costume_id 会被
置空）与 RESTRICT 阻塞点（characters.actor_id 仍引用公共 actor），便于审阅。

只读，不改任何数据，可重复执行。用法：
    uv run python -m app.cli.export_public_assets [输出目录]
默认输出到 backups/public_assets/ 下按时间戳命名的文件。
"""

from __future__ import annotations

import asyncio
import enum
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_maker
from app.models.studio import (
    Actor,
    ActorImage,
    Character,
    CharacterPropLink,
    Costume,
    CostumeImage,
    ProjectActorLink,
    ProjectCostumeLink,
    ProjectPropLink,
    ProjectSceneLink,
    Prop,
    PropImage,
    Scene,
    SceneImage,
    ShotDetail,
)

_DEFAULT_OUTPUT_DIR = Path("backups/public_assets")


def _row_to_dict(obj: object) -> dict[str, Any]:
    """把 ORM 行转成列名→值的普通字典（JSON 序列化交给 _json_default）。"""
    columns = type(obj).__table__.columns.keys()  # type: ignore[attr-defined]
    return {name: getattr(obj, name) for name in columns}


def _json_default(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"不可序列化的类型：{type(value)!r}")


async def _rows(db: AsyncSession, model: type, where: Any) -> list[dict[str, Any]]:
    result = await db.execute(select(model).where(where))
    return [_row_to_dict(row) for row in result.scalars().all()]


async def collect_public_assets(db: AsyncSession) -> dict[str, Any]:
    """收集 project_id 为空的四类资产及其级联从属、SET NULL/RESTRICT 关联行。"""
    scenes = await _rows(db, Scene, Scene.project_id.is_(None))
    props = await _rows(db, Prop, Prop.project_id.is_(None))
    costumes = await _rows(db, Costume, Costume.project_id.is_(None))
    actors = await _rows(db, Actor, Actor.project_id.is_(None))

    scene_ids = [row["id"] for row in scenes]
    prop_ids = [row["id"] for row in props]
    costume_ids = [row["id"] for row in costumes]
    actor_ids = [row["id"] for row in actors]

    children = {
        "scene_images": await _rows(db, SceneImage, SceneImage.scene_id.in_(scene_ids)),
        "project_scene_links": await _rows(db, ProjectSceneLink, ProjectSceneLink.scene_id.in_(scene_ids)),
        "prop_images": await _rows(db, PropImage, PropImage.prop_id.in_(prop_ids)),
        "project_prop_links": await _rows(db, ProjectPropLink, ProjectPropLink.prop_id.in_(prop_ids)),
        "character_prop_links": await _rows(db, CharacterPropLink, CharacterPropLink.prop_id.in_(prop_ids)),
        "costume_images": await _rows(db, CostumeImage, CostumeImage.costume_id.in_(costume_ids)),
        "project_costume_links": await _rows(db, ProjectCostumeLink, ProjectCostumeLink.costume_id.in_(costume_ids)),
        "actor_images": await _rows(db, ActorImage, ActorImage.actor_id.in_(actor_ids)),
        "project_actor_links": await _rows(db, ProjectActorLink, ProjectActorLink.actor_id.in_(actor_ids)),
    }

    # 副作用/阻塞点：删除时 shot_details.scene_id、characters.costume_id 会被 SET NULL；
    # characters.actor_id 是 RESTRICT，仍被引用则删除会被挡（purge 脚本据此中止）。
    details_null = await db.execute(
        select(ShotDetail.id, ShotDetail.scene_id).where(ShotDetail.scene_id.in_(scene_ids))
    )
    chars_costume = await db.execute(
        select(Character.id, Character.costume_id).where(Character.costume_id.in_(costume_ids))
    )
    chars_actor = await db.execute(
        select(Character.id, Character.actor_id).where(Character.actor_id.in_(actor_ids))
    )
    side_effects = {
        "shot_details_scene_id_set_null": [{"id": r.id, "scene_id": r.scene_id} for r in details_null],
        "characters_costume_id_set_null": [{"id": r.id, "costume_id": r.costume_id} for r in chars_costume],
        "characters_actor_id_restrict": [{"id": r.id, "actor_id": r.actor_id} for r in chars_actor],
    }

    return {
        "assets": {"scenes": scenes, "props": props, "costumes": costumes, "actors": actors},
        "children": children,
        "side_effects": side_effects,
    }


def summarize(bundle: dict[str, Any]) -> str:
    assets = bundle["assets"]
    children = bundle["children"]
    blockers = bundle["side_effects"]["characters_actor_id_restrict"]
    parts = [f"{name}={len(rows)}" for name, rows in assets.items()]
    child_total = sum(len(rows) for rows in children.values())
    line = f"公共资产：{' '.join(parts)}；级联从属共 {child_total} 行"
    if blockers:
        line += f"；⚠️ {len(blockers)} 个 character 仍引用公共 actor（RESTRICT，清理会被挡）"
    return line


async def _run(output_dir: Path, now: datetime) -> None:
    async with async_session_maker() as db:
        bundle = await collect_public_assets(db)

    bundle = {"exported_at": now.isoformat(), **bundle}
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"public_assets_{now:%Y%m%d_%H%M%S}.json"
    out_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    print(summarize(bundle))
    print(f"已导出到：{out_path}")


def main() -> None:
    output_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_OUTPUT_DIR
    asyncio.run(_run(output_dir, datetime.now()))


if __name__ == "__main__":
    main()
