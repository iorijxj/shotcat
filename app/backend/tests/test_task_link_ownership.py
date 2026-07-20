"""阶段四 4.1 跨用户越权回归测试：GenerationTaskLink 任务链接系统的多态归属校验。

用户 A 的任务/任务关联，用户 B 必须查不到、改不了、删不掉；project_id 为空的历史资产
（scene/prop/costume/actor）视为公共资产，登录用户均可访问。用真实内存 SQLite（同
test_project_ownership.py 的模式），让归属反查走真实 SQL。
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models.auth  # noqa: F401  # 确保 metadata 注册
import app.models.studio  # noqa: F401
from app.core.db import Base
from app.dependencies import get_current_user, get_db
from app.main import app
from app.models.auth import User
from app.models.studio import (
    Chapter,
    ChapterStatus,
    Project,
    ProjectStyle,
    ProjectVisualStyle,
    Scene,
    SceneImage,
    Shot,
    ShotFrameImage,
    ShotStatus,
)
from app.models.task import GenerationDeliveryMode, GenerationTask, GenerationTaskStatus
from app.models.task_links import GenerationTaskLink, GenerationTaskLinkStatus
from app.models.types import ShotFrameType

USER_A = User(id="user-a", username="alice", password_hash="x")
USER_B = User(id="user-b", username="bob", password_hash="x")


def _project(project_id: str, owner_id: str) -> Project:
    return Project(
        id=project_id,
        owner_id=owner_id,
        name="项目",
        description="",
        style=ProjectStyle.real_people_city,
        visual_style=ProjectVisualStyle.live_action,
        seed=0,
        unify_style=True,
        progress=0,
        stats={},
    )


def _chapter(chapter_id: str, project_id: str) -> Chapter:
    return Chapter(
        id=chapter_id,
        project_id=project_id,
        index=1,
        title="第一章",
        summary="",
        raw_text="",
        condensed_text="",
        storyboard_count=0,
        status=ChapterStatus.draft,
    )


def _shot(shot_id: str, chapter_id: str) -> Shot:
    return Shot(
        id=shot_id,
        chapter_id=chapter_id,
        index=1,
        title="镜头",
        thumbnail="",
        status=ShotStatus.pending,
        script_excerpt="",
    )


def _scene(scene_id: str, project_id: str | None) -> Scene:
    return Scene(
        id=scene_id,
        name="场景",
        description="",
        style=ProjectStyle.real_people_city,
        view_count=1,
        tags=[],
        visual_style=ProjectVisualStyle.live_action,
        project_id=project_id,
    )


def _scene_image(image_id: int, scene_id: str) -> SceneImage:
    return SceneImage(id=image_id, scene_id=scene_id)


def _shot_frame_image(image_id: int, shot_detail_id: str) -> ShotFrameImage:
    return ShotFrameImage(id=image_id, shot_detail_id=shot_detail_id, frame_type=ShotFrameType.first)


def _task(task_id: str) -> GenerationTask:
    return GenerationTask(
        id=task_id,
        mode=GenerationDeliveryMode.async_polling,
        task_kind="script_divide",
        status=GenerationTaskStatus.succeeded,
        progress=100,
        payload={},
    )


def _link(link_id: int, task_id: str, relation_type: str, relation_entity_id: str) -> GenerationTaskLink:
    return GenerationTaskLink(
        id=link_id,
        task_id=task_id,
        resource_type="task_link",
        relation_type=relation_type,
        relation_entity_id=relation_entity_id,
        status=GenerationTaskLinkStatus.todo,
    )


class _Harness:
    def __init__(self) -> None:
        self._engine = create_async_engine("sqlite+aiosqlite://")
        self._maker: async_sessionmaker | None = None

    def seed(self, *objs: object) -> None:
        async def _run() -> None:
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            self._maker = async_sessionmaker(self._engine, expire_on_commit=False)
            async with self._maker() as session:
                for obj in objs:
                    session.add(obj)
                await session.commit()

        asyncio.run(_run())

    def request_as(self, user: User) -> TestClient:
        maker = self._maker

        async def _get_db():
            async with maker() as session:
                yield session

        async def _get_current_user() -> User:
            return user

        app.dependency_overrides[get_db] = _get_db
        app.dependency_overrides[get_current_user] = _get_current_user
        return TestClient(app)

    def close(self) -> None:
        app.dependency_overrides.clear()
        asyncio.run(self._engine.dispose())


# --- 单条任务接口（经 task_id 反查归属）---


def test_user_b_cannot_get_task_status_via_chapter_link() -> None:
    h = _Harness()
    h.seed(
        _project("proj-a", USER_A.id),
        _chapter("ch-a", "proj-a"),
        _task("task-a"),
        _link(1, "task-a", "chapter_division", "ch-a"),
    )
    try:
        resp = h.request_as(USER_B).get("/api/v1/film/tasks/task-a/status")
    finally:
        h.close()
    assert resp.status_code == 404


def test_user_a_can_get_own_task_status_via_chapter_link() -> None:
    h = _Harness()
    h.seed(
        _project("proj-a", USER_A.id),
        _chapter("ch-a", "proj-a"),
        _task("task-a"),
        _link(1, "task-a", "chapter_division", "ch-a"),
    )
    try:
        resp = h.request_as(USER_A).get("/api/v1/film/tasks/task-a/status")
    finally:
        h.close()
    assert resp.status_code == 200
    assert resp.json()["data"]["task_id"] == "task-a"


def test_user_b_cannot_get_task_result_via_shot_link() -> None:
    h = _Harness()
    h.seed(
        _project("proj-a", USER_A.id),
        _chapter("ch-a", "proj-a"),
        _shot("shot-a", "ch-a"),
        _task("task-a"),
        _link(1, "task-a", "video", "shot-a"),
    )
    try:
        resp = h.request_as(USER_B).get("/api/v1/film/tasks/task-a/result")
    finally:
        h.close()
    assert resp.status_code == 404


def test_user_b_cannot_cancel_task_via_shot_link() -> None:
    h = _Harness()
    h.seed(
        _project("proj-a", USER_A.id),
        _chapter("ch-a", "proj-a"),
        _shot("shot-a", "ch-a"),
        _task("task-a"),
        _link(1, "task-a", "shot_first_frame_prompt", "shot-a"),
    )
    try:
        resp = h.request_as(USER_B).post("/api/v1/film/tasks/task-a/cancel", json={})
    finally:
        h.close()
    assert resp.status_code == 404


def test_task_without_any_link_is_forbidden() -> None:
    """无任何关联记录的 task 无法确定归属，一律 fail-closed。"""
    h = _Harness()
    h.seed(_task("task-orphan"))
    try:
        resp = h.request_as(USER_A).get("/api/v1/film/tasks/task-orphan/status")
    finally:
        h.close()
    assert resp.status_code == 404


def test_unknown_relation_type_is_forbidden() -> None:
    h = _Harness()
    h.seed(_task("task-a"), _link(1, "task-a", "some_unknown_type", "whatever"))
    try:
        resp = h.request_as(USER_A).get("/api/v1/film/tasks/task-a/status")
    finally:
        h.close()
    assert resp.status_code == 404


# --- 公共资产 vs 归属资产（image 系反查）---


def test_null_project_scene_image_task_not_visible() -> None:
    """P3 起 project_id 为空不再视为公共资产（历史公共资产已清理）：
    这类 scene image 反查任务对任何用户都不可见，返回 404。"""
    h = _Harness()
    h.seed(
        _scene("scene-pub", None),
        _scene_image(1, "scene-pub"),
        _task("task-a"),
        _link(1, "task-a", "scene_image", "1"),
    )
    try:
        resp = h.request_as(USER_B).get("/api/v1/film/tasks/task-a/status")
    finally:
        h.close()
    assert resp.status_code == 404


def test_owned_scene_image_task_hidden_from_others() -> None:
    """scene 归属 A 的项目时，B 看不到。"""
    h = _Harness()
    h.seed(
        _project("proj-a", USER_A.id),
        _scene("scene-a", "proj-a"),
        _scene_image(1, "scene-a"),
        _task("task-a"),
        _link(1, "task-a", "scene_image", "1"),
    )
    try:
        resp = h.request_as(USER_B).get("/api/v1/film/tasks/task-a/status")
    finally:
        h.close()
    assert resp.status_code == 404


def test_shot_frame_image_link_ownership() -> None:
    h = _Harness()
    h.seed(
        _project("proj-a", USER_A.id),
        _chapter("ch-a", "proj-a"),
        _shot("shot-a", "ch-a"),
        _shot_frame_image(1, "shot-a"),
        _task("task-a"),
        _link(1, "task-a", "shot_frame_image", "1"),
    )
    try:
        resp = h.request_as(USER_B).get("/api/v1/film/tasks/task-a/status")
    finally:
        h.close()
    assert resp.status_code == 404


def test_chapter_or_project_relation_type_via_project() -> None:
    """relation_entity_id 是 project_id（consistency_check 走 chapter-or-project 分支）。"""
    h = _Harness()
    h.seed(
        _project("proj-a", USER_A.id),
        _task("task-a"),
        _link(1, "task-a", "consistency_check", "proj-a"),
    )
    try:
        resp_b = h.request_as(USER_B).get("/api/v1/film/tasks/task-a/status")
        resp_a = h.request_as(USER_A).get("/api/v1/film/tasks/task-a/status")
    finally:
        h.close()
    assert resp_b.status_code == 404
    assert resp_a.status_code == 200


# --- task-link CRUD 越权 ---


def test_user_b_cannot_get_user_a_task_link() -> None:
    h = _Harness()
    h.seed(
        _project("proj-a", USER_A.id),
        _chapter("ch-a", "proj-a"),
        _task("task-a"),
        _link(1, "task-a", "chapter_division", "ch-a"),
    )
    try:
        resp = h.request_as(USER_B).get("/api/v1/film/task-links/1")
    finally:
        h.close()
    assert resp.status_code == 404


def test_user_b_cannot_delete_user_a_task_link() -> None:
    h = _Harness()
    h.seed(
        _project("proj-a", USER_A.id),
        _chapter("ch-a", "proj-a"),
        _task("task-a"),
        _link(1, "task-a", "chapter_division", "ch-a"),
    )
    try:
        resp = h.request_as(USER_B).delete("/api/v1/film/task-links/1")
        verify = h.request_as(USER_A).get("/api/v1/film/task-links/1")
    finally:
        h.close()
    assert resp.status_code == 404
    assert verify.status_code == 200  # 未被删掉


def test_user_b_cannot_update_user_a_task_link() -> None:
    h = _Harness()
    h.seed(
        _project("proj-a", USER_A.id),
        _chapter("ch-a", "proj-a"),
        _task("task-a"),
        _link(1, "task-a", "chapter_division", "ch-a"),
    )
    try:
        resp = h.request_as(USER_B).patch("/api/v1/film/task-links/1", json={"status": "accepted"})
    finally:
        h.close()
    assert resp.status_code == 404


def test_create_task_link_to_others_entity_forbidden() -> None:
    """B 不能把任务关联到 A 的章节。"""
    h = _Harness()
    h.seed(_project("proj-a", USER_A.id), _chapter("ch-a", "proj-a"), _task("task-a"))
    try:
        resp = h.request_as(USER_B).post(
            "/api/v1/film/task-links",
            json={
                "task_id": "task-a",
                "resource_type": "task_link",
                "relation_type": "chapter_division",
                "relation_entity_id": "ch-a",
            },
        )
    finally:
        h.close()
    assert resp.status_code == 404


def test_adopt_task_link_ownership() -> None:
    h = _Harness()
    h.seed(
        _project("proj-a", USER_A.id),
        _chapter("ch-a", "proj-a"),
        _task("task-a"),
        _link(1, "task-a", "chapter_division", "ch-a"),
    )
    try:
        resp = h.request_as(USER_B).patch(
            "/api/v1/film/task-links/adopt",
            json={"task_id": "task-a", "chapter_id": "ch-a"},
        )
    finally:
        h.close()
    assert resp.status_code == 404


def test_list_task_links_filters_out_others() -> None:
    """列表页内过滤：B 只看到自己的关联。"""
    h = _Harness()
    h.seed(
        _project("proj-a", USER_A.id),
        _project("proj-b", USER_B.id),
        _chapter("ch-a", "proj-a"),
        _chapter("ch-b", "proj-b"),
        _task("task-a"),
        _task("task-b"),
        _link(1, "task-a", "chapter_division", "ch-a"),
        _link(2, "task-b", "chapter_division", "ch-b"),
    )
    try:
        resp = h.request_as(USER_B).get("/api/v1/film/task-links")
    finally:
        h.close()
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()["data"]["items"]]
    assert ids == [2]
