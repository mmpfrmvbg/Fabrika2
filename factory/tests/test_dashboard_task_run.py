"""POST /api/tasks/<id>/run: accept_dashboard_task_run и отказ по статусу."""

from __future__ import annotations

import sqlite3


from factory.dashboard_task_run import accept_dashboard_task_run
from factory.db import gen_id, init_db


def _insert_ready_atom(conn, wid: str, *, status: str = "ready_for_work") -> None:
    now = "2026-03-30T12:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority,
            created_at, updated_at
        )
        VALUES (?, ?, ?, 'vision', 'V', '', 'planned', 'creator', 'creator', 0, 1, ?, ?)
        """,
        ("vis_tr", None, "vis_tr", now, now),
    )
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority,
            created_at, updated_at
        )
        VALUES (?, ?, ?, 'atom', 'Atom', '', ?, 'creator', 'forge', 1, 1, ?, ?)
        """,
        (wid, "vis_tr", "vis_tr", status, now, now),
    )
    conn.execute(
        """
        INSERT INTO work_item_files (id, work_item_id, path, intent, description, required)
        VALUES (?, ?, 'factory/hello_qwen.py', 'modify', 'x', 1)
        """,
        (gen_id("wif"), wid),
    )
    conn.execute(
        """
        INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at)
        VALUES (?, 'forge_inbox', 10, ?)
        """,
        (wid, now),
    )


def test_accept_dashboard_task_run_denied_bad_status(monkeypatch, tmp_path) -> None:
    db = tmp_path / "dr_denied.db"
    monkeypatch.setenv("FACTORY_DB_PATH", str(db))
    conn = init_db(db)
    now = "2026-03-30T12:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority,
            created_at, updated_at
        )
        VALUES (?, NULL, ?, 'atom', 'X', '', 'done', 'c', 'p', 1, 1, ?, ?)
        """,
        ("atom_done", "atom_done", now, now),
    )
    conn.commit()
    conn.close()

    ok, data, code = accept_dashboard_task_run("atom_done")
    assert ok is False
    assert code == 400
    assert data.get("ok") is False
    assert "error" in data

    conn = init_db(db)
    n = conn.execute(
        """
        SELECT COUNT(*) AS c FROM event_log
        WHERE work_item_id = ? AND event_type = 'dashboard.task_run_denied'
        """,
        ("atom_done",),
    ).fetchone()["c"]
    conn.close()
    assert int(n) >= 1


def test_accept_dashboard_task_run_happy_path(monkeypatch, tmp_path) -> None:
    db = tmp_path / "dr_ok.db"
    monkeypatch.setenv("FACTORY_DB_PATH", str(db))

    conn = init_db(db)
    _insert_ready_atom(conn, "atom_ready")
    conn.commit()
    conn.close()

    ok, data, code = accept_dashboard_task_run("atom_ready")
    assert ok is True
    assert code == 200
    assert data.get("ok") is True
    assert data.get("status") == "enqueued"
    assert not data.get("run_id")

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    n_req = conn.execute(
        """
        SELECT COUNT(*) AS c FROM event_log
        WHERE work_item_id = ? AND event_type = 'dashboard.task_run_requested'
        """,
        ("atom_ready",),
    ).fetchone()["c"]
    assert int(n_req) >= 1
    st = conn.execute(
        "SELECT status FROM work_items WHERE id = ?", ("atom_ready",)
    ).fetchone()["status"]
    q = conn.execute(
        "SELECT queue_name, lease_owner FROM work_item_queue WHERE work_item_id = ?",
        ("atom_ready",),
    ).fetchone()
    conn.close()
    assert st == "ready_for_work"
    assert q is not None
    assert q["queue_name"] == "forge_inbox"
    assert q["lease_owner"] is None


def test_accept_dashboard_non_atom_returns_400(monkeypatch, tmp_path) -> None:
    db = tmp_path / "dr_nonatom.db"
    monkeypatch.setenv("FACTORY_DB_PATH", str(db))
    conn = init_db(db)
    now = "2026-03-30T12:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        )
        VALUES ('vis1', NULL, 'vis1', 'vision', 'V', '', 'planned', 'c', 'p', 0, 1, ?, ?)
        """,
        (now, now),
    )
    conn.commit()
    conn.close()
    ok, data, code = accept_dashboard_task_run("vis1")
    assert ok is False
    assert code == 400
    assert "atom" in (data.get("error") or "").lower()


def test_accept_dashboard_409_in_progress(monkeypatch, tmp_path) -> None:
    db = tmp_path / "dr_409.db"
    monkeypatch.setenv("FACTORY_DB_PATH", str(db))
    conn = init_db(db)
    now = "2026-03-30T12:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        )
        VALUES ('atom_ip', NULL, 'atom_ip', 'atom', 'A', '', 'in_progress', 'c', 'forge', 1, 1, ?, ?)
        """,
        (now, now),
    )
    conn.commit()
    conn.close()
    ok, data, code = accept_dashboard_task_run("atom_ip")
    assert ok is False
    assert code == 409
