"""CLI --create-vision / --add-atom на временной БД."""

from __future__ import annotations

import sqlite3

from factory.cli_vision import run_add_atom, run_create_vision
from factory.composition import wire


def test_create_vision_and_add_atom(tmp_path) -> None:
    db = tmp_path / "t.db"
    factory = wire(db)
    factory["conn"].execute("PRAGMA foreign_keys=ON")
    factory["conn"].commit()
    factory["conn"].close()

    vid = run_create_vision(db, "V title", "V desc")
    assert vid.startswith("vis_")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    r = conn.execute(
        "SELECT kind, title FROM work_items WHERE id = ?", (vid,)
    ).fetchone()
    assert r["kind"] == "vision"
    assert r["title"] == "V title"
    conn.close()

    aid = run_add_atom(
        db,
        vid,
        "Atom one",
        "Do thing",
        files_csv="pkg/foo.py",
        file_intent="modify",
    )
    assert aid.startswith("ato_")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    st = conn.execute(
        "SELECT status FROM work_items WHERE id = ?", (aid,)
    ).fetchone()["status"]
    assert st == "ready_for_work"
    pr = conn.execute(
        """
        SELECT priority FROM work_item_queue
        WHERE work_item_id = ? AND queue_name = 'forge_inbox'
        """,
        (aid,),
    ).fetchone()
    assert pr is not None
    assert int(pr["priority"]) == 1
    conn.close()
