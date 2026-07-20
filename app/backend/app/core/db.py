"""SQLAlchemy 异步引擎与会话。"""

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session

from app.config import settings


class CrossTenantWriteError(RuntimeError):
    """向 session 租户上下文之外写入聚合根：不可能的越权写，直接拒绝。"""


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


@event.listens_for(Session, "before_flush")
def _stamp_tenant_on_flush(session: Session, _flush_context: object, _instances: object) -> None:
    """聚合根写入盖章（多租户 M2 P2）。

    仅当 session 带租户上下文（get_current_tenant 已写 session.info["tenant_id"]）时生效：
    新增聚合根未带 tenant_id 则自动盖上；显式带了且与上下文不一致则拒绝（越权写）。
    无租户上下文（CLI/Celery/未接入门禁的测试）一律放行，保证既有行为不变。
    读过滤在 P4 单独打开。
    """
    tenant_id = session.info.get("tenant_id")
    if tenant_id is None:
        return
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
