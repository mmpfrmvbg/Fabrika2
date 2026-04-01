"""Контракт mark_atom_ready_for_forge + select_next_atom_for_forge (без Qwen)."""

from __future__ import annotations

from factory.composition import wire
from factory.forge_next_atom import mark_atom_ready_for_forge, select_next_atom_for_forge
from factory.models import QueueName, WorkItemStatus
from factory.work_items import WorkItemOps


def test_mark_atom_ready_for_forge_then_select(tmp_path) -> None:
    db = tmp_path / "t.db"
    f = wire(db)
    conn = f["conn"]
    ops = WorkItemOps(conn, f["logger"])
    vid = ops.create_vision("V", "D")
    eid = ops.create_child(vid, "epic", "E", "e")
    aid = ops.create_child(
        eid,
        "atom",
        "A",
        "a",
        files=[{"path": "factory/x.py", "intent": "modify", "description": None}],
    )
    mark_atom_ready_for_forge(conn, f["sm"], aid, orchestrator=f["orchestrator"])
    row = select_next_atom_for_forge(conn)
    assert row is not None
    assert row["id"] == aid
    assert row["status"] == WorkItemStatus.READY_FOR_WORK.value
    assert len(row["files"]) == 1

    q = conn.execute(
        "SELECT queue_name FROM work_item_queue WHERE work_item_id = ?",
        (aid,),
    ).fetchone()
    assert q["queue_name"] == QueueName.FORGE_INBOX.value


def test_mark_atom_ready_for_forge_idempotent(tmp_path) -> None:
    db = tmp_path / "t.db"
    f = wire(db)
    conn = f["conn"]
    ops = WorkItemOps(conn, f["logger"])
    vid = ops.create_vision("V", "D")
    eid = ops.create_child(vid, "epic", "E", "e")
    aid = ops.create_child(
        eid,
        "atom",
        "A",
        "a",
        files=[{"path": "a.py", "intent": "modify", "description": None}],
    )
    mark_atom_ready_for_forge(conn, f["sm"], aid, orchestrator=f["orchestrator"])
    mark_atom_ready_for_forge(conn, f["sm"], aid, orchestrator=f["orchestrator"])
