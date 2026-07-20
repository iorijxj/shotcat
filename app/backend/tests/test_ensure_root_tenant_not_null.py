"""根实体 tenant_id NOT NULL 迁移 CLI 回归测试（多租户 M2 P4a）。

覆盖：8 表仍有 tenant_id 为空时中止（提示先回填）；无残留则放行；幂等。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from app.cli.ensure_root_tenant_not_null import RootTenantNullError, ensure_root_tenant_not_null

_ROOT_TABLES = ("projects", "scenes", "props", "costumes", "actors", "characters", "providers", "models")


def _legacy_engine(*, nullable: bool, seed_null_in: str | None = None) -> object:
    """建 8 张只含 tenant_id 列的旧表；nullable 决定列是否可空，可选塞一行 NULL。"""
    engine = create_engine("sqlite:///:memory:")
    constraint = "NULL" if nullable else "NOT NULL"
    with engine.begin() as conn:
        for table in _ROOT_TABLES:
            conn.execute(text(f"CREATE TABLE {table} (id VARCHAR(64) PRIMARY KEY, tenant_id VARCHAR(64) {constraint})"))
        if seed_null_in:
            conn.execute(text(f"INSERT INTO {seed_null_in} (id, tenant_id) VALUES ('x', NULL)"))
    return engine


def test_migration_aborts_when_null_tenant_remains() -> None:
    engine = _legacy_engine(nullable=True, seed_null_in="providers")
    with engine.begin() as conn:
        with pytest.raises(RootTenantNullError, match="providers=1"):
            ensure_root_tenant_not_null(conn)
    engine.dispose()


def test_migration_passes_and_is_idempotent_without_null() -> None:
    engine = _legacy_engine(nullable=False)
    with engine.begin() as conn:
        assert ensure_root_tenant_not_null(conn) == []  # 非 MySQL 只校验、不 ALTER
    with engine.begin() as conn:
        assert ensure_root_tenant_not_null(conn) == []
    engine.dispose()
