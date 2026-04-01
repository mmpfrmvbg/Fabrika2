"""Контракт /api/tasks/tree: kind, label, architect_comment."""

from __future__ import annotations

import sqlite3

from factory.dashboard_api import (
    _latest_architect_comments,
    _normalize_kind,
    _work_items_with_files,
)
from factory.db import gen_id, init_db


def test_normalize_kind_aliases_and_fallback() -> None:
    assert _normalize_kind("vision") == ("vision", "Vision")
    assert _normalize_kind("STORY") == ("story", "Story")
    assert _normalize_kind("atm_change") == ("atom", "Atom")
    assert _normalize_kind("initiative") == ("story", "Story")
    assert _normalize_kind("unknown_xyz") == ("task", "Task")


def test_work_items_tree_fields(tmp_path) -> None:
    db = tmp_path / "tree_test.db"
    conn = init_db(db)
    now = "2026-03-30T12:00:00.000000Z"
    v, s, e, t, a = "wi_v", "wi_s", "wi_e", "wi_t", "wi_a"
    rows = [
        (v, None, v, "vision", "V", 0),
        (s, v, v, "story", "S", 1),
        (e, s, v, "epic", "E", 2),
        (t, e, v, "task", "T", 3),
        (a, t, v, "atom", "A", 4),
    ]
    for wid, pid, rid, kind, title, depth in rows:
        conn.execute(
            """
            INSERT INTO work_items (
                id, parent_id, root_id, kind, title, description, status,
                creator_role, owner_role, planning_depth, priority,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, '', 'planned', 'creator', 'planner', ?, 1, ?, ?)
            """,
            (wid, pid, rid, kind, title, depth, now, now),
        )
    cid = gen_id("ac")
    conn.execute(
        """
        INSERT INTO architect_comments (id, work_item_id, comment, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (cid, v, "Architect note on vision", now),
    )
    conn.commit()
    conn.close()

    ro = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    items = _work_items_with_files(ro)
    ro.close()

    by_id = {x["id"]: x for x in items}
    assert by_id[v]["kind"] == "vision"
    assert by_id[v]["label"] == "Vision"
    assert by_id[v]["level"] == 0
    assert by_id[v]["architect_comment"] == "Architect note on vision"
    assert by_id[a]["kind"] == "atom"
    assert by_id[a]["label"] == "Atom"
    assert by_id[a]["level"] == 4
    assert by_id[s]["architect_comment"] is None


def test_work_items_last_event_run_count_and_step(tmp_path) -> None:
    """Поля last_event, run_count, last_step для дерева задач."""
    db = tmp_path / "live_fields.db"
    conn = init_db(db)
    now = "2026-03-30T12:00:00.000000Z"
    wid = "wi_atom"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority,
            created_at, updated_at
        )
        VALUES (?, NULL, ?, 'atom', 'A', '', 'in_review', 'c', 'p', 4, 1, ?, ?)
        """,
        (wid, wid, now, now),
    )
    conn.execute(
        """
        INSERT INTO event_log (
            event_time, event_type, entity_type, entity_id, message, work_item_id, severity
        )
        VALUES (?, 'forge.completed', 'work_item', ?, 'forge ok', ?, 'info')
        """,
        (now, wid, wid),
    )
    conn.execute(
        """
        INSERT INTO runs (id, work_item_id, agent_id, role, run_type, status, started_at, finished_at)
        VALUES ('run_x', ?, 'agent_forge', 'forge', 'forge', 'completed', ?, ?)
        """,
        (wid, now, now),
    )
    conn.execute(
        """
        INSERT INTO run_steps (id, run_id, step_no, step_kind, status, summary, payload, created_at)
        VALUES ('rs1', 'run_x', 1, 'file_write', 'completed', 'wrote file', '{}', ?)
        """,
        (now,),
    )
    conn.commit()
    conn.close()

    ro = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    items = _work_items_with_files(ro)
    ro.close()

    a = next(x for x in items if x["id"] == wid)
    assert a["run_count"] == 1
    assert a["last_event"]["event_type"] == "forge.completed"
    assert a["last_step"]["step_kind"] == "file_write"


def test_architect_comments_table_optional_ro(tmp_path) -> None:
    """Если таблицы нет, API не падает."""
    db = tmp_path / "no_arch.db"
    conn = init_db(db)
    conn.execute("DROP TABLE IF EXISTS architect_comments")
    conn.commit()
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority,
            created_at, updated_at
        )
        VALUES ('x', NULL, 'x', 'vision', 't', '', 'planned', 'c', 'p', 0, 1, 't', 't')
        """
    )
    conn.commit()
    conn.close()
    ro = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    assert _latest_architect_comments(ro) == {}
    ro.close()
