"""Дашборд: дочерние задачи, transition, forge-run (тот же runner, что /run)."""

from __future__ import annotations

import sqlite3

from factory.dashboard_task_children import post_create_child
from factory.dashboard_task_run import accept_dashboard_task_run
from factory.dashboard_task_transition import post_task_transition
from factory.db import init_db


def _ins_vision(conn: sqlite3.Connection, vid: str = "vis_op") -> None:
    now = "2026-03-30T12:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        )
        VALUES (?, NULL, ?, 'vision', 'V', '', 'draft', 'creator', 'creator', 0, 1, ?, ?)
        """,
        (vid, vid, now, now),
    )


def test_post_child_epic_under_vision(monkeypatch, tmp_path) -> None:
    db = tmp_path / "ch.db"
    monkeypatch.setenv("FACTORY_DB_PATH", str(db))
    conn = init_db(db)
    _ins_vision(conn)
    conn.commit()
    conn.close()

    ok, data, code = post_create_child("vis_op", {"title": "E1", "description": "d"})
    assert ok is True
    assert code == 201
    assert data.get("kind") == "epic"

    conn = init_db(db)
    row = conn.execute(
        "SELECT parent_id, kind FROM work_items WHERE id = ?", (data["id"],)
    ).fetchone()
    conn.close()
    assert row["parent_id"] == "vis_op"
    assert row["kind"] == "epic"


def test_post_child_atom_requires_files(monkeypatch, tmp_path) -> None:
    db = tmp_path / "at.db"
    monkeypatch.setenv("FACTORY_DB_PATH", str(db))
    conn = init_db(db)
    now = "2026-03-30T12:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        )
        VALUES ('t1', NULL, 'v1', 'task', 'T', '', 'draft', 'creator', 'creator', 3, 1, ?, ?)
        """,
        (now, now),
    )
    conn.commit()
    conn.close()

    ok, data, code = post_create_child("t1", {"title": "A", "kind": "atom"})
    assert ok is False
    assert code == 400
    assert "files" in (data.get("error") or "").lower()

    ok2, data2, code2 = post_create_child(
        "t1",
        {"title": "A2", "kind": "atom", "files": ["factory/hello_qwen.py"]},
    )
    assert ok2 is True
    assert code2 == 201
    aid = data2["id"]
    conn = init_db(db)
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM work_item_files WHERE work_item_id = ?", (aid,)
    ).fetchone()["c"]
    conn.close()
    assert int(n) == 1


def test_post_transition_creator_submitted(monkeypatch, tmp_path) -> None:
    db = tmp_path / "tr.db"
    monkeypatch.setenv("FACTORY_DB_PATH", str(db))
    conn = init_db(db)
    _ins_vision(conn, "vis_tr2")
    conn.commit()
    conn.close()

    ok, data, code = post_task_transition("vis_tr2", {"event": "creator_submitted"})
    assert ok is True
    assert code == 200
    assert data.get("status") == "planned"


def test_forge_run_same_as_run_accept(monkeypatch, tmp_path) -> None:
    """accept_dashboard_task_run один для /run и /forge-run."""
    db = tmp_path / "fr.db"
    monkeypatch.setenv("FACTORY_DB_PATH", str(db))
    conn = init_db(db)
    now = "2026-03-30T12:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        )
        VALUES ('vis_fr', NULL, 'vis_fr', 'vision', 'V', '', 'planned', 'creator', 'creator', 0, 1, ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        )
        VALUES ('atm_fr', 'vis_fr', 'vis_fr', 'atom', 'A', '', 'ready_for_work', 'creator', 'forge', 1, 1, ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO work_item_files (id, work_item_id, path, intent, description, required)
        VALUES ('wif_x', 'atm_fr', 'x.py', 'modify', '', 0)
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("FACTORY_QWEN_DRY_RUN", "1")
    ok, payload, status = accept_dashboard_task_run("atm_fr")
    assert ok is True
    assert status == 200
    assert payload.get("status") == "enqueued"
    assert not payload.get("run_id")
