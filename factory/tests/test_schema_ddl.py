from __future__ import annotations

import sqlite3

from factory import db_migrations
from factory.schema_ddl import DDL, V_API_USAGE_TODAY_RECREATE


EXPECTED_TABLES = {
    "agents",
    "work_items",
    "state_transitions",
    "event_log",
    "runs",
}

EXPECTED_VIEWS = {
    "v_dashboard",
    "v_api_usage_today",
    "v_active_runs",
    "v_recent_errors",
}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {name for (name,) in rows}


def _view_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='view'").fetchall()
    return {name for (name,) in rows}


def test_ddl_constants_are_not_empty() -> None:
    assert DDL.strip()
    assert "CREATE TABLE" in DDL

    assert V_API_USAGE_TODAY_RECREATE.strip()
    assert "DROP VIEW IF EXISTS v_api_usage_today" in V_API_USAGE_TODAY_RECREATE


def test_ensure_schema_creates_core_tables(tmp_path) -> None:
    db_path = tmp_path / "schema_ddl.db"

    db_migrations.ensure_schema(db_path)

    conn = sqlite3.connect(db_path)
    try:
        assert EXPECTED_TABLES.issubset(_table_names(conn))
    finally:
        conn.close()


def test_ensure_schema_loads_fsm_seed(tmp_path) -> None:
    db_path = tmp_path / "schema_ddl_fsm.db"

    db_migrations.ensure_schema(db_path)

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT from_state, event_name, to_state FROM state_transitions WHERE id = 'st_01'"
        ).fetchone()
        assert row == ("draft", "creator_submitted", "planned")
    finally:
        conn.close()


def test_ddl_creates_views_and_views_are_queryable() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(DDL)

        assert EXPECTED_VIEWS.issubset(_view_names(conn))

        for view_name in EXPECTED_VIEWS:
            conn.execute(f"SELECT * FROM {view_name} LIMIT 1").fetchall()
    finally:
        conn.close()
