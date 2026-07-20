"""租户 schema 幂等迁移 CLI 回归测试（多租户 M2 P0）。

模拟"旧库 users 表无 default_tenant_id 列"，验证 _sync_ensure 会建租户新表、
补上缺失列，且可重复执行不报错。
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from app.cli.ensure_tenant_schema import _sync_ensure

_OLD_USERS_DDL = (
    "CREATE TABLE users ("
    "id VARCHAR(64) PRIMARY KEY, username VARCHAR(64), password_hash VARCHAR(255), "
    "created_at DATETIME, updated_at DATETIME)"
)


def test_ensure_adds_missing_column_and_tables(tmp_path) -> None:  # noqa: ANN001
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text(_OLD_USERS_DDL))  # 旧 schema：users 无 default_tenant_id

    with engine.begin() as conn:
        _sync_ensure(conn)
        columns = {c["name"] for c in inspect(conn).get_columns("users")}
        assert "default_tenant_id" in columns
        tables = set(inspect(conn).get_table_names())
        assert {"tenants", "tenant_memberships"} <= tables

    # 幂等：新库已含列，二次执行不应报错
    with engine.begin() as conn:
        _sync_ensure(conn)
    engine.dispose()
