from __future__ import annotations

import signal
from pathlib import Path

from factory.db import init_db
from factory.models import EventType
from factory.queue_ops import claim_forge_inbox_atom
from factory.worker import run_worker_loop, worker_iteration
import factory.worker as worker_module


def test_claim_forge_inbox_prefers_higher_work_item_priority(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "priority_claim.db")
    now = "2026-04-01T00:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        ) VALUES ('atom_low', NULL, 'atom_low', 'atom', 'Low', '', 'ready_for_work',
                  'creator', 'forge', 0, 1, ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        ) VALUES ('atom_high', NULL, 'atom_high', 'atom', 'High', '', 'ready_for_work',
                  'creator', 'forge', 0, 10, ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at)
        VALUES ('atom_low', 'forge_inbox', 100, ?)
        """,
        (now,),
    )
    conn.execute(
        """
        INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at)
        VALUES ('atom_high', 'forge_inbox', 100, ?)
        """,
        (now,),
    )
    conn.commit()

    claimed = claim_forge_inbox_atom(conn, "worker-prio")
    assert claimed == "atom_high"


def test_sigterm_during_processing_finishes_item_and_stops_cleanly(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "graceful.db"
    conn = init_db(db_path)
    now = "2026-04-01T00:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        ) VALUES ('atom1', NULL, 'atom1', 'atom', 'A1', '', 'ready_for_work',
                  'creator', 'forge', 0, 1, ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at)
        VALUES ('atom1', 'forge_inbox', 100, ?)
        """,
        (now,),
    )
    conn.commit()

    captured_handlers: dict[int, object] = {}

    def fake_signal(sig, handler):
        captured_handlers[sig] = handler

    class DummyLogger:
        def __init__(self):
            self.messages: list[str] = []

        def log(self, event_type, entity_type, entity_id, message, **kwargs):
            if event_type == EventType.TASK_STATUS_CHANGED:
                self.messages.append(message)

    logger = DummyLogger()

    factory = {"conn": conn, "logger": logger, "orchestrator": object(), "db_path": str(db_path)}
    monkeypatch.setattr(worker_module, "wire", lambda _: factory)
    monkeypatch.setattr(worker_module, "resolve_db_path", lambda: db_path)
    monkeypatch.setattr(worker_module.signal, "signal", fake_signal)

    calls = {"n": 0}

    def fake_worker_iteration(_factory, _worker_id):
        calls["n"] += 1
        if calls["n"] == 1:
            conn.execute("UPDATE work_items SET status='in_progress' WHERE id='atom1'")
            conn.execute(
                "UPDATE work_item_queue SET lease_owner='worker-1', lease_until='2026-04-01T00:30:00Z' WHERE work_item_id='atom1'"
            )
            conn.commit()
            captured_handlers[signal.SIGTERM](signal.SIGTERM, None)
            conn.execute("UPDATE work_items SET status='done' WHERE id='atom1'")
            conn.execute("UPDATE work_item_queue SET lease_owner=NULL, lease_until=NULL WHERE work_item_id='atom1'")
            conn.commit()
            return True
        return False

    monkeypatch.setattr(worker_module, "worker_iteration", fake_worker_iteration)

    run_worker_loop(worker_id="worker-1", poll_sec=0.01)

    verify_conn = init_db(db_path)
    st = verify_conn.execute("SELECT status FROM work_items WHERE id='atom1'").fetchone()["status"]
    lease_owner = verify_conn.execute(
        "SELECT lease_owner FROM work_item_queue WHERE work_item_id='atom1'"
    ).fetchone()["lease_owner"]
    verify_conn.close()
    assert st == "done"
    assert lease_owner is None
    assert any("Worker shutting down gracefully, finishing current item..." in m for m in logger.messages)
    assert any("Worker stopped cleanly" in m for m in logger.messages)


def test_worker_iteration_marks_forge_failed_when_forge_crashes(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "worker_forge_crash.db"
    conn = init_db(db_path)
    now = "2026-04-01T00:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        ) VALUES ('atom_fail', NULL, 'atom_fail', 'atom', 'Failing atom', '', 'ready_for_work',
                  'creator', 'forge', 0, 1, ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at)
        VALUES ('atom_fail', 'forge_inbox', 100, ?)
        """,
        (now,),
    )
    conn.commit()
    conn.close()

    factory = worker_module.wire(db_path)

    def fake_forge_crash(_orch):
        raise RuntimeError("boom in forge worker")

    monkeypatch.setattr(worker_module.forge, "run_forge_queued_runs", fake_forge_crash)

    worked = worker_iteration(factory, "worker-crash")

    assert worked is True
    row = factory["conn"].execute(
        """
        SELECT wi.status, wiq.lease_owner, wiq.attempts
        FROM work_items wi
        LEFT JOIN work_item_queue wiq ON wiq.work_item_id = wi.id
        WHERE wi.id = 'atom_fail'
        """
    ).fetchone()
    assert row is not None
    assert row["status"] in {"ready_for_work", "dead"}
    assert row["status"] != "in_progress"
    assert row["lease_owner"] is None
    assert int(row["attempts"] or 0) >= 1


def test_worker_iteration_marks_failed_actual_batch_run_item(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "worker_forge_batch_target.db"
    conn = init_db(db_path)
    now = "2026-04-01T00:00:00.000000Z"

    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        ) VALUES ('atom_claimed', NULL, 'atom_claimed', 'atom', 'Claimed atom', '', 'ready_for_work',
                  'creator', 'forge', 0, 10, ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at)
        VALUES ('atom_claimed', 'forge_inbox', 100, ?)
        """,
        (now,),
    )

    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        ) VALUES ('atom_real_fail', NULL, 'atom_real_fail', 'atom', 'Really failed atom', '', 'in_progress',
                  'creator', 'forge', 0, 1, ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at, lease_owner, lease_until)
        VALUES ('atom_real_fail', 'forge_inbox', 100, ?, 'worker-batch', '2026-04-01T01:00:00.000000Z')
        """,
        (now,),
    )
    conn.commit()
    conn.close()

    factory = worker_module.wire(db_path)

    def fake_forge_batch_crash(_orch):
        raise worker_module.forge.ForgeBatchRunError(
            "atom_real_fail",
            "run_real_fail",
            RuntimeError("boom in batch run"),
        )

    monkeypatch.setattr(worker_module.forge, "run_forge_queued_runs", fake_forge_batch_crash)

    worked = worker_iteration(factory, "worker-batch")

    assert worked is True

    claimed_status = factory["conn"].execute(
        "SELECT status FROM work_items WHERE id = 'atom_claimed'"
    ).fetchone()["status"]
    failed_status = factory["conn"].execute(
        "SELECT status FROM work_items WHERE id = 'atom_real_fail'"
    ).fetchone()["status"]
    failed_lease_owner = factory["conn"].execute(
        "SELECT lease_owner FROM work_item_queue WHERE work_item_id = 'atom_real_fail'"
    ).fetchone()["lease_owner"]

    assert claimed_status == "in_progress"
    assert failed_status in {"ready_for_work", "dead"}
    assert failed_lease_owner is None
