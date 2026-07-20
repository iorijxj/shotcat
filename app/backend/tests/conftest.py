"""Pytest 共享 fixture：FastAPI 应用与 TestClient。"""

from __future__ import annotations

import asyncio
import inspect
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

try:
    from app.main import app  # type: ignore
    from app.dependencies import get_current_user
    from app.models.auth import User
except Exception:  # noqa: BLE001
    # 测试环境里有些可选依赖（例如 langgraph）可能未安装。
    # 不要让整个测试套件在导入 conftest 时直接失败；仅在需要 client 的测试里跳过。
    app = None
    get_current_user = None
    User = None


# 除跨用户越权测试外，其余 *_api_responses 测试只关心响应壳/业务逻辑，不关心鉴权语义，
# 统一伪造一个登录用户，各测试文件的 Fake DB 里凡是需要归属校验通过的 Project，
# owner_id 都应设为这个 id（见各文件 _seed_project 之类的 helper）。
TEST_USER_ID = "test-user"


@contextmanager
def assets_project_id_nullable():
    """create_all 期间临时把四类资产 project_id 设为可空（还原在 finally）。

    P3 把 scene/prop/costume/actor 的 project_id 转成了 NOT NULL；但清理前的
    export/purge/backfill 等迁移 CLI 本就跑在"迁移前的旧库"上（project_id 尚可空、
    仍有 NULL 残留）。这些 CLI 的测试需要建出旧 schema 才能塞 NULL 行验证清理逻辑。
    """
    from app.models.studio import Actor, Costume, Prop, Scene

    cols = [m.__table__.c.project_id for m in (Scene, Prop, Costume, Actor)]
    originals = [c.nullable for c in cols]
    for col in cols:
        col.nullable = True
    try:
        yield
    finally:
        for col, original in zip(cols, originals):
            col.nullable = original


class AlwaysOwnedGetMixin:
    """给不关心归属校验细节、只测响应壳/业务逻辑的 Fake DB 提供通用 get()。

    Shot/Chapter/Project 之间自动串成属于 TEST_USER_ID 的链条（不管传入的 id 是什么），
    让 app.services.auth.ownership 里的 assert_*_owned 直接放行，不用逐个测试文件手动
    维护真实的项目/章节/镜头归属数据。仅用于"测别的、不测越权"的既有测试；跨用户越权
    行为本身的测试见 test_project_ownership.py，那边用真实的 owner_id 精确断言。
    """

    async def get(self, model, entity_id):  # noqa: ANN001
        from app.models.studio import (
            Actor,
            Chapter,
            Character,
            Costume,
            Project,
            Prop,
            Scene,
            Shot,
            ShotExtractedCandidate,
            ShotExtractedDialogueCandidate,
        )

        if model is Project:
            return SimpleNamespace(id=entity_id, owner_id=TEST_USER_ID)
        if model is Chapter:
            return SimpleNamespace(id=entity_id, project_id=f"{entity_id}::project")
        if model is Shot:
            return SimpleNamespace(id=entity_id, chapter_id=f"{entity_id}::chapter")
        if model in {ShotExtractedCandidate, ShotExtractedDialogueCandidate}:
            return SimpleNamespace(id=entity_id, shot_id="shot-1")
        if model in {Character, Scene, Prop, Costume, Actor}:
            # 测试里不关心这几类资产的归属细节，挂到属于 TEST_USER_ID 的项目上放行
            # （P3 起 project_id 必非空，Project 分支对任意 id 都返回 TEST_USER_ID 所有）。
            return SimpleNamespace(id=entity_id, project_id=f"{entity_id}::project")
        return None


@pytest.fixture(autouse=True)
def _reset_generation_rate_limit():
    """生成类限流按进程内存计数；测试不带 token 时统一落到 client IP 维度，
    跨测试累计会误触 429，故每个测试前后重置。"""
    from app.core.rate_limit import reset_generation_rate_limit

    reset_generation_rate_limit()
    yield
    reset_generation_rate_limit()


@pytest.fixture
def client() -> TestClient:
    """FastAPI 应用 TestClient，用于集成测试。默认已登录（见 TEST_USER_ID）。"""
    if app is None:
        pytest.skip("FastAPI app 依赖未满足（例如缺少 langgraph），跳过需要 client 的集成测试。")

    async def _fake_current_user() -> User:
        return User(id=TEST_USER_ID, username="test", password_hash="x")

    app.dependency_overrides[get_current_user] = _fake_current_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def pytest_configure(config: pytest.Config) -> None:
    """为轻量测试环境补齐 asyncio marker。"""
    config.addinivalue_line("markers", "asyncio: mark test as asyncio coroutine")


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    """在未安装 pytest-asyncio 的环境中兜底执行 async 测试。"""

    if not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None

    funcargs = {
        arg: pyfuncitem.funcargs[arg]
        for arg in pyfuncitem._fixtureinfo.argnames
        if arg in pyfuncitem.funcargs
    }
    asyncio.run(pyfuncitem.obj(**funcargs))
    return True
