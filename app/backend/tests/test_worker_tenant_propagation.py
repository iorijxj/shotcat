"""P4b：Celery/后台任务的租户透传回归测试。

验证「入队时把 tenant_id 写进 payload → worker 把 tenant_id 盖回 session.info →
后台新建根实体经 before_flush 盖上正确 tenant」这条链路，且对无租户上下文的既有
行为零影响。读过滤（P4c）尚未打开，这里只锁定透传管道本身。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, CrossTenantWriteError, worker_tenant_scope
from app.core.task_manager.stores import SqlAlchemyTaskStore
from app.core.task_manager.types import DeliveryMode
from app.models.types import ProjectStyle


def _import_models() -> None:
    import app.models.auth  # noqa: F401
    import app.models.tenant  # noqa: F401
    import app.models.llm  # noqa: F401
    import app.models.studio  # noqa: F401
    import app.models.task  # noqa: F401
    import app.models.task_links  # noqa: F401


def _new_sync_maker() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    _import_models()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def _new_project(**overrides: object):
    from app.models.studio import Project

    fields: dict[str, object] = {
        "id": "p-1",
        "name": "样例项目",
        "style": next(iter(ProjectStyle)),
    }
    fields.update(overrides)
    return Project(**fields)


def test_after_begin_stamps_tenant_only_inside_scope() -> None:
    maker = _new_sync_maker()

    with worker_tenant_scope("t1"):
        with maker() as db:
            db.execute(select(1))
            assert db.info.get("tenant_id") == "t1"

    # 退出 scope 后新 session 不应带租户上下文
    with maker() as db:
        db.execute(select(1))
        assert db.info.get("tenant_id") is None


def test_background_root_entity_gets_tenant_stamped() -> None:
    maker = _new_sync_maker()

    with worker_tenant_scope("t1"):
        with maker() as db:
            db.add(_new_project())
            db.commit()
            row = db.get(type(_new_project()), "p-1")
            assert row is not None
            assert row.tenant_id == "t1"


def test_background_cross_tenant_write_is_rejected() -> None:
    maker = _new_sync_maker()

    with worker_tenant_scope("t1"):
        with maker() as db:
            db.add(_new_project(tenant_id="t2"))
            with pytest.raises(CrossTenantWriteError):
                db.commit()


def test_empty_scope_is_noop() -> None:
    """tenant_id 为空时不设上下文，既有行为不变。"""
    maker = _new_sync_maker()

    with worker_tenant_scope(None):
        with maker() as db:
            db.execute(select(1))
            assert db.info.get("tenant_id") is None


async def test_store_create_injects_tenant_into_payload() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    _import_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        db.info["tenant_id"] = "t1"
        store = SqlAlchemyTaskStore(db)
        record = await store.create(
            payload={"task_kind": "x", "run_args": {}},
            mode=DeliveryMode.async_polling,
            task_kind="x",
        )
        assert record.payload.get("tenant_id") == "t1"

    async with maker() as db:
        store = SqlAlchemyTaskStore(db)
        record = await store.create(
            payload={"task_kind": "y", "run_args": {}},
            mode=DeliveryMode.async_polling,
            task_kind="y",
        )
        assert "tenant_id" not in record.payload


def test_worker_tenant_scope_propagates_into_asyncio_run() -> None:
    """AbstractAsyncDelegatingExecutor 在同步 run() 里 asyncio.run(runner)，
    contextvar 必须随 context 拷贝进入新 loop，after_begin 才能盖 async session。"""

    async def _inner() -> str | None:
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            await db.execute(select(1))
            return db.info.get("tenant_id")

    with worker_tenant_scope("t-async"):
        result = asyncio.run(_inner())
    assert result == "t-async"
