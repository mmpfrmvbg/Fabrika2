from __future__ import annotations

import signal
from pathlib import Path

from factory.db import init_db
from factory.models import EventType
from factory.queue_ops import claim_forge_inbox_atom
from factory.worker import recover_stuck_running_work_items, run_worker_loop, worker_iteration
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

    claimed_row = factory["conn"].execute(
        """
        SELECT wi.status, wiq.lease_owner
        FROM work_items wi
        LEFT JOIN work_item_queue wiq ON wiq.work_item_id = wi.id
        WHERE wi.id = 'atom_claimed'
        """
    ).fetchone()
    failed_status = factory["conn"].execute(
        "SELECT status FROM work_items WHERE id = 'atom_real_fail'"
    ).fetchone()["status"]
    failed_lease_owner = factory["conn"].execute(
        "SELECT lease_owner FROM work_item_queue WHERE work_item_id = 'atom_real_fail'"
    ).fetchone()["lease_owner"]

    assert claimed_row is not None
    assert claimed_row["status"] == "in_progress"
    assert claimed_row["lease_owner"] is None
    assert failed_status in {"ready_for_work", "dead"}
    assert failed_lease_owner is None


def test_claim_forge_inbox_reclaims_expired_lease(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "expired_lease_claim.db")
    now = "2026-04-01T00:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        ) VALUES ('atom_expired', NULL, 'atom_expired', 'atom', 'Expired lease atom', '', 'ready_for_work',
                  'creator', 'forge', 0, 1, ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO work_item_queue (
            work_item_id, queue_name, priority, available_at, lease_owner, lease_until
        )
        VALUES (
            'atom_expired', 'forge_inbox', 100, strftime('%Y-%m-%dT%H:%M:%f','now','-1 minute'),
            'dead-worker', strftime('%Y-%m-%dT%H:%M:%f','now','-10 minutes')
        )
        """
    )
    conn.commit()

    claimed = claim_forge_inbox_atom(conn, "worker-reclaimer")
    assert claimed == "atom_expired"


def test_recover_stuck_running_uses_seconds_timeout_below_minute(tmp_path: Path, monkeypatch) -> None:
    conn = init_db(tmp_path / "stuck_seconds_timeout.db")
    logger = worker_module.FactoryLogger(conn)
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            previous_status, creator_role, owner_role, planning_depth, priority, last_heartbeat_at
        )
        VALUES (
            'wi_secs', NULL, 'wi_secs', 'atom', 'Secs', '', 'running',
            'ready_for_work', 'planner', 'forge', 0, 100,
            strftime('%Y-%m-%dT%H:%M:%f','now','-40 seconds')
        )
        """
    )
    conn.commit()
    monkeypatch.setattr(worker_module, "_STUCK_WORK_ITEM_TIMEOUT_SEC", 30)

    recovered = recover_stuck_running_work_items(conn, logger, worker_id="worker-seconds")
    conn.commit()

    assert recovered == 1
    row = conn.execute("SELECT status FROM work_items WHERE id='wi_secs'").fetchone()
    assert row["status"] == "ready_for_work"


def test_worker_iteration_orphan_path_starts_heartbeat(tmp_path: Path, monkeypatch) -> None:
    class FakeConn:
        def __init__(self) -> None:
            self.commits = 0

        def execute(self, _sql: str, _params=()):
            class _Row:
                def fetchone(self_inner):
                    return {"work_item_id": "wi_orphan"}

            return _Row()

        def commit(self) -> None:
            self.commits += 1

    fake_conn = FakeConn()
    factory = {"conn": fake_conn, "logger": object(), "orchestrator": object(), "db_path": str(tmp_path / "x.db")}
    touched: list[str] = []
    hb_loops: list[str] = []
    downstream: list[str] = []

    monkeypatch.setattr(worker_module, "claim_forge_inbox_atom", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_module, "_apply_worker_sqlite_pragmas", lambda _conn: None)
    monkeypatch.setattr(worker_module, "_touch_work_item_heartbeat", lambda _conn, wi_id: touched.append(wi_id))

    class _HB:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        worker_module,
        "_heartbeat_loop",
        lambda _db_path, wi_id: hb_loops.append(wi_id) or _HB(),
    )
    monkeypatch.setattr(worker_module.forge, "run_forge_queued_runs", lambda _orch: None)
    monkeypatch.setattr(worker_module, "drain_atom_downstream", lambda _orch, wi_id: downstream.append(wi_id))

    worked = worker_iteration(factory, "worker-orphan")

    assert worked is True
    assert touched == ["wi_orphan"]
    assert hb_loops == ["wi_orphan"]
    assert downstream == ["wi_orphan"]
