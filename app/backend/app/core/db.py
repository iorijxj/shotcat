"""SQLAlchemy 异步引擎与会话。"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, with_loader_criteria
from sqlalchemy.orm.session import ORMExecuteState

from app.config import settings


class CrossTenantWriteError(RuntimeError):
    """向 session 租户上下文之外写入聚合根：不可能的越权写，直接拒绝。"""


# 后台任务（Celery/local-thread）运行在请求之外，不经 get_current_tenant。
# 用 contextvar 承载当前后台任务的租户，配合下方 after_begin 事件把 tenant_id
# 盖进每个 session.info，使 before_flush 盖章与（P4c 的）读过滤在后台同样生效。
# 请求路径不设该 contextvar（恒为 None），互不干扰。
_worker_tenant_ctx: ContextVar[str | None] = ContextVar("worker_tenant_id", default=None)


@contextmanager
def worker_tenant_scope(tenant_id: str | None) -> Iterator[None]:
    """在后台任务入口声明当前租户；tenant_id 为空时为 no-op（保持既有行为）。"""
    if not tenant_id:
        yield
        return
    token = _worker_tenant_ctx.set(tenant_id)
    try:
        yield
    finally:
        _worker_tenant_ctx.reset(token)


def _build_engine() -> AsyncEngine:
    connect_args: dict[str, Any] = {}
    if settings.database_url.startswith("sqlite"):
        # SQLite 默认几乎不等待锁；批量图片任务并发写入时会把普通读取也打成 500。
        connect_args = {"timeout": 30}
    db_engine = create_async_engine(
        settings.database_url,
        echo=settings.debug,
        future=True,
        # MySQL wait_timeout（默认 8h）会踢掉空闲连接，池里的死连接会导致
        # "Lost connection to MySQL server during query"；pre_ping 取用前探活，
        # recycle 提前主动换连接兜底
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args=connect_args,
    )
    if settings.database_url.startswith("sqlite"):
        _configure_sqlite_connection(db_engine)
    return db_engine


def _configure_sqlite_connection(db_engine: AsyncEngine) -> None:
    """为每条 SQLite 连接启用 WAL 和锁等待，允许读请求与短写事务并存。"""

    @event.listens_for(db_engine.sync_engine, "connect")
    def set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA busy_timeout = 30000")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = NORMAL")
            # SQLite 默认不强制外键；不开则 ON DELETE CASCADE/SET NULL 全部失效，
            # 删聚合根会留下 *_images / *_links 孤儿行（多租户 P3 清理依赖级联）。
            cursor.execute("PRAGMA foreign_keys = ON")
        finally:
            cursor.close()


def _build_session_maker(bind_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


class _AsyncSessionMakerProxy:
    """可重绑定的 sessionmaker 代理。

    Celery prefork 模式下，worker 子进程不能继续复用父进程里初始化的
    async engine / sessionmaker。这里保持导入对象稳定，同时允许在子进程
    启动后重新绑定底层 sessionmaker。
    """

    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker

    def configure(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker

    def __call__(self, *args: Any, **kwargs: Any) -> AsyncSession:
        return self._maker(*args, **kwargs)


engine = _build_engine()
async_session_maker = _AsyncSessionMakerProxy(_build_session_maker(engine))


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""

    pass


@event.listens_for(Session, "after_begin")
def _bind_worker_tenant_on_begin(session: Session, _transaction: object, _connection: object) -> None:
    """后台任务：事务开始时把 contextvar 里的租户盖进 session.info（多租户 M2 P4b）。

    仅当 contextvar 有值且 session 尚未由请求路径（get_current_tenant）盖过时生效。
    sync/async session 底层都是 Session，事件挂在 Session 类上两者都覆盖。
    """
    if session.info.get("tenant_id") is not None:
        return
    tenant_id = _worker_tenant_ctx.get()
    if tenant_id is not None:
        session.info["tenant_id"] = tenant_id


@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_read_filter(orm_execute_state: ORMExecuteState) -> None:
    """租户读过滤（多租户 M2 P4c）：session 有租户上下文时，自动给所有
    TenantScopedMixin 查询注入 tenant_id == tid，含 join/懒加载的别名。

    仅作用于 SELECT，跳过列刷新（column_load）与关系加载（relationship_load）——
    这是 SQLAlchemy 官方推荐的守卫，避免与既有 join 语句冲突；子实体不带该 mixin，
    经根到达即可。开发者忘写 where 也默认安全。
    """
    if (
        not orm_execute_state.is_select
        or orm_execute_state.is_column_load
        or orm_execute_state.is_relationship_load
    ):
        return
    tenant_id = orm_execute_state.session.info.get("tenant_id")
    if tenant_id is None:
        return
    from app.models.base import TenantScopedMixin

    orm_execute_state.statement = orm_execute_state.statement.options(
        with_loader_criteria(
            TenantScopedMixin,
            lambda cls: cls.tenant_id == tenant_id,
            include_aliases=True,
        )
    )


@event.listens_for(Session, "before_flush")
def _stamp_tenant_on_flush(session: Session, _flush_context: object, _instances: object) -> None:
    """聚合根写入盖章（多租户 M2）。

    仅当 session 带租户上下文（get_current_tenant 或 worker_tenant_scope 已写
    session.info["tenant_id"]）时生效：新增聚合根未带 tenant_id 则自动盖上；显式带了
    且与上下文不一致则拒绝（越权写）。无租户上下文（CLI/迁移脚本）一律放行。
    与 do_orm_execute 读过滤（P4c）配套构成写盖章 + 读过滤的双层隔离。
    """
    tenant_id = session.info.get("tenant_id")
    if tenant_id is None:
        # after_begin 在同一次 flush 中晚于 before_flush 触发，写路径这里兜底
        # 从 contextvar 取后台租户并回盖 session.info（幂等）。
        tenant_id = _worker_tenant_ctx.get()
        if tenant_id is None:
            return
        session.info["tenant_id"] = tenant_id
    from app.models.base import TenantScopedMixin

    for obj in session.new:
        if not isinstance(obj, TenantScopedMixin):
            continue
        if obj.tenant_id is None:
            obj.tenant_id = tenant_id
        elif obj.tenant_id != tenant_id:
            raise CrossTenantWriteError(
                f"拒绝跨租户写入：对象 tenant_id={obj.tenant_id!r} 与当前上下文 {tenant_id!r} 不一致"
            )


async def init_db() -> None:
    """创建所有表（开发/迁移用）。"""
    # 确保 ORM 模型已导入，从而注册到 Base.metadata
    import app.models.auth  # noqa: F401
    import app.models.tenant  # noqa: F401
    import app.models.llm  # noqa: F401  # pylint: disable=unused-import
    import app.models.studio  # noqa: F401
    import app.models.task  # noqa: F401
    import app.models.task_links  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """关闭数据库连接。"""
    await engine.dispose()


def reset_db_runtime() -> None:
    """在 Celery worker 子进程中重建 engine 与 sessionmaker。

    这样可以避免 prefork 继承父进程中的 async engine，导致连接对象和事件循环
    绑定错乱，触发 Future attached to a different loop。
    """

    global engine

    engine = _build_engine()
    async_session_maker.configure(_build_session_maker(engine))
