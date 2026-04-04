from __future__ import annotations

import sqlite3

import pytest

from factory.fsm import StateMachine
from factory.guards import Guards


class _DummyActions:
    pass


class _DummyLogger:
    def log(self, *_args, **_kwargs) -> None:
        return None


def _build_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE state_transitions (
            id TEXT PRIMARY KEY,
            entity_type TEXT,
            from_state TEXT,
            event_name TEXT,
            to_state TEXT,
            guard_name TEXT,
            applicable_kinds TEXT
        );

        CREATE TABLE work_items (
            id TEXT PRIMARY KEY,
            status TEXT,
            kind TEXT,
            creator_role TEXT,
            owner_role TEXT,
            retry_count INTEGER DEFAULT 0,
            max_retries INTEGER DEFAULT 3
        );

        CREATE TABLE work_item_files (
            id TEXT PRIMARY KEY,
            work_item_id TEXT,
            path TEXT,
            intent TEXT
        );

        CREATE TABLE review_results (
            id TEXT PRIMARY KEY,
            work_item_id TEXT,
            verdict TEXT,
            created_at TEXT
        );

        CREATE TABLE file_changes (
            id TEXT PRIMARY KEY,
            work_item_id TEXT
        );
        """
    )
    return conn


def test_can_transition_with_valid_guard() -> None:
    conn = _build_conn()
    conn.execute(
        "INSERT INTO work_items(id, status, kind, creator_role, owner_role) VALUES (?, ?, ?, ?, ?)",
        ("wi-1", "draft", "atom", "planner", "planner"),
    )
    conn.execute(
        "INSERT INTO work_item_files(id, work_item_id, path, intent) VALUES (?, ?, ?, ?)",
        ("f-1", "wi-1", "src/a.py", "modify"),
    )
    conn.execute(
        "INSERT INTO state_transitions(id, entity_type, from_state, event_name, to_state, guard_name, applicable_kinds) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("t-1", "work_item", "draft", "forge_inbox", "ready_for_work", "guard_has_files_declared", '["atom"]'),
    )

    sm = StateMachine(conn, Guards(conn), _DummyActions(), _DummyLogger())
    ok, to_state = sm.can_transition("wi-1", "forge_inbox")

    assert ok is True
    assert to_state == "ready_for_work"


def test_can_transition_guard_blocks_rule() -> None:
    conn = _build_conn()
    conn.execute(
        "INSERT INTO work_items(id, status, kind, creator_role, owner_role) VALUES (?, ?, ?, ?, ?)",
        ("wi-2", "draft", "atom", "planner", "planner"),
    )
    conn.execute(
        "INSERT INTO state_transitions(id, entity_type, from_state, event_name, to_state, guard_name, applicable_kinds) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("t-2", "work_item", "draft", "forge_inbox", "ready_for_work", "guard_has_files_declared", '["atom"]'),
    )

    sm = StateMachine(conn, Guards(conn), _DummyActions(), _DummyLogger())
    ok, message = sm.can_transition("wi-2", "forge_inbox")

    assert ok is False
    assert "Нет перехода" in message


def test_invalid_transition_guard_raises_error() -> None:
    conn = _build_conn()
    conn.execute(
        "INSERT INTO work_items(id, status, kind, creator_role, owner_role) VALUES (?, ?, ?, ?, ?)",
        ("wi-3", "draft", "atom", "planner", "planner"),
    )
    conn.execute(
        "INSERT INTO state_transitions(id, entity_type, from_state, event_name, to_state, guard_name, applicable_kinds) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("t-3", "work_item", "draft", "bad_event", "ready_for_work", "guard_does_not_exist", '["atom"]'),
    )

    sm = StateMachine(conn, Guards(conn), _DummyActions(), _DummyLogger())

    with pytest.raises(ValueError, match="Неизвестный guard"):
        sm.can_transition("wi-3", "bad_event")
