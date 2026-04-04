"""Проверка, что ensure_schema создаёт необходимые performance-индексы SQLite."""
from __future__ import annotations

from pathlib import Path

from factory.db import ensure_schema, get_connection


def test_ensure_schema_creates_expected_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "factory_indexes.db"
    ensure_schema(db_path)
    conn = get_connection(db_path)
    try:
        expected = {
            "idx_work_items_status",
            "idx_work_items_parent_id",
            "idx_work_items_created_at",
            "idx_runs_work_item_id",
            "idx_runs_created_at",
            "idx_event_log_entity_id",
            "idx_event_log_event_type",
            "idx_event_log_created_at",
        }
        existing = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert expected.issubset(existing), f"Missing indexes: {expected - existing}"
    finally:
        conn.close()
