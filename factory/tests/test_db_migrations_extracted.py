from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from factory import db, db_migrations
from factory.schema_ddl import DDL
from factory.schema_ddl_aux import MIGRATIONS_TABLE_DDL


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {name for (name,) in rows}


def _normalized_sql(sql: str) -> str:
    return " ".join(sql.split()).lower()


def test_ensure_schema_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "idempotent_schema.db"

    db_migrations.ensure_schema(db_path)
    db_migrations.ensure_schema(db_path)

    conn = sqlite3.connect(db_path)
    try:
        table_names = _table_names(conn)
        assert "migrations" in table_names
        assert "improvement_candidates" in table_names
        assert "architect_comments" in table_names
    finally:
        conn.close()


def test_migrate_schema_creates_expected_tables() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(DDL)
        db_migrations.migrate_schema(conn)

        table_names = _table_names(conn)
        assert "architect_comments" in table_names
        assert "judge_verdicts" in table_names
        assert "review_results" in table_names
    finally:
        conn.close()


def test_migrations_table_ddl_matches_ensure_schema_result(tmp_path: Path) -> None:
    db_path = tmp_path / "migrations_table_ddl.db"
    db_migrations.ensure_schema(db_path)

    conn_real = sqlite3.connect(db_path)
    conn_expected = sqlite3.connect(":memory:")
    try:
        conn_expected.executescript(MIGRATIONS_TABLE_DDL)

        real_sql = conn_real.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='migrations'"
        ).fetchone()
        expected_sql = conn_expected.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='migrations'"
        ).fetchone()

        assert real_sql is not None
        assert expected_sql is not None
        assert _normalized_sql(real_sql[0]) == _normalized_sql(expected_sql[0])
    finally:
        conn_real.close()
        conn_expected.close()


def test_migrate_schema_available_from_both_modules() -> None:
    assert callable(db.migrate_schema)
    assert callable(db_migrations.migrate_schema)
    assert db.migrate_schema is db_migrations.migrate_schema


def test_migrate_schema_delegates_aux_schema_builders(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(DDL)
    called: dict[str, int] = {"architect": 0, "judge_review": 0}

    def _architect(conn: sqlite3.Connection) -> None:
        called["architect"] += 1

    def _judge_review(conn: sqlite3.Connection) -> None:
        called["judge_review"] += 1

    monkeypatch.setattr(db_migrations, "create_architect_comments_schema", _architect)
    monkeypatch.setattr(db_migrations, "create_judge_and_review_results_schema", _judge_review)
    try:
        db_migrations.migrate_schema(conn)
        assert called == {"architect": 1, "judge_review": 1}
    finally:
        conn.close()
