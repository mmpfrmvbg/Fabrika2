"""POST /api/tasks/<id>/comments."""

from __future__ import annotations

from factory.dashboard_task_comments import post_task_comment
from factory.db import init_db


def test_post_comment_happy(monkeypatch, tmp_path) -> None:
    db = tmp_path / "cmt.db"
    monkeypatch.setenv("FACTORY_DB_PATH", str(db))
    conn = init_db(db)
    now = "2026-03-30T12:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        )
        VALUES ('w1', NULL, 'w1', 'vision', 'V', '', 'planned', 'creator', 'creator', 0, 1, ?, ?)
        """,
        (now, now),
    )
    conn.commit()
    conn.close()

    ok, data, code = post_task_comment("w1", "creator", "hello note")
    assert ok is True
    assert code == 201
    assert data.get("comment_id")

    conn = init_db(db)
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM comments WHERE work_item_id = ?", ("w1",)
    ).fetchone()["c"]
    ev = conn.execute(
        "SELECT COUNT(*) AS c FROM event_log WHERE event_type = 'comment.added' AND work_item_id = ?",
        ("w1",),
    ).fetchone()["c"]
    conn.close()
    assert int(n) == 1
    assert int(ev) >= 1


def test_post_comment_not_found(monkeypatch, tmp_path) -> None:
    db = tmp_path / "cmt2.db"
    monkeypatch.setenv("FACTORY_DB_PATH", str(db))
    init_db(db)
    ok, data, code = post_task_comment("nope", "creator", "x")
    assert ok is False
    assert code == 404
