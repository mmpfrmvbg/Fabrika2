from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factory.api_server import app
from factory.composition import wire
from factory.db import init_db
from factory.logging import FactoryLogger
from factory.models import QueueName
from factory.worker import recover_stuck_running_work_items


@pytest.fixture
def wired_factory(tmp_path: Path):
    f = wire(tmp_path / "orchestration_failures.db")
    try:
        yield f
    finally:
        f["conn"].close()


def _insert_atom(conn: sqlite3.Connection, wi_id: str, *, status: str = "ready_for_work") -> None:
    now = "2026-03-30T12:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            previous_status, creator_role, owner_role, planning_depth, priority,
            created_at, updated_at
        )
        VALUES (?, NULL, ?, 'atom', 'A', '', ?, 'ready_for_work', 'creator', 'forge', 1, 10, ?, ?)
        """,
        (wi_id, wi_id, status, now, now),
    )


def test_worker_crash_startup_recovery_clears_queue_lease_and_reenqueues_on_tick(wired_factory) -> None:
    conn = wired_factory["conn"]
    logger = FactoryLogger(conn)

    _insert_atom(conn, "wi_crash", status="running")
    conn.execute(
        """
        INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at, attempts, max_attempts, lease_owner, lease_until)
        VALUES ('wi_crash', ?, 10, strftime('%Y-%m-%dT%H:%M:%f','now'), 0, 3, 'worker-1', strftime('%Y-%m-%dT%H:%M:%f','now','+30 minute'))
        """,
        (QueueName.FORGE_INBOX.value,),
    )
    conn.commit()

    recovered = recover_stuck_running_work_items(conn, logger, worker_id="worker-startup")
    conn.commit()

    assert recovered == 1
    wi = conn.execute("SELECT status, previous_status FROM work_items WHERE id='wi_crash'").fetchone()
    q = conn.execute("SELECT lease_owner, lease_until FROM work_item_queue WHERE work_item_id='wi_crash'").fetchone()
    assert wi["status"] == "ready_for_work"
    assert wi["previous_status"] == "running"
    # Startup recovery removes stale queue rows for crashed running work;
    # the orchestrator re-enqueues ready atoms on the next tick.
    assert q is None

    wired_factory["orchestrator"]._auto_enqueue_ready_atoms()
    conn.commit()
    q_reenqueued = conn.execute("SELECT queue_name, lease_owner, lease_until FROM work_item_queue WHERE work_item_id='wi_crash'").fetchone()
    assert q_reenqueued is not None
    assert q_reenqueued["queue_name"] == QueueName.FORGE_INBOX.value
    assert q_reenqueued["lease_owner"] is None
    assert q_reenqueued["lease_until"] is None


def test_dead_letter_exhaustion_marks_item_dead(wired_factory) -> None:
    conn = wired_factory["conn"]
    orch = wired_factory["orchestrator"]

    _insert_atom(conn, "wi_dead")
    conn.execute(
        """
        INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at, attempts, max_attempts)
        VALUES ('wi_dead', ?, 10, strftime('%Y-%m-%dT%H:%M:%f','now'), 0, 3)
        """,
        (QueueName.COMPLETION_INBOX.value,),
    )
    conn.commit()

    def _always_fail(_item: dict) -> None:
        raise RuntimeError("boom")

    orch._process_queue(QueueName.COMPLETION_INBOX, _always_fail)
    conn.commit()

    row = conn.execute("SELECT status, dead_at FROM work_items WHERE id='wi_dead'").fetchone()
    q = conn.execute("SELECT attempts, lease_owner FROM work_item_queue WHERE work_item_id='wi_dead'").fetchone()
    assert q is not None
    assert row["status"] == "dead"
    assert row["dead_at"] is not None
    assert q["attempts"] == 3
    assert q["lease_owner"] is None


def test_lease_expiry_reclaims_stale_queue_lease(wired_factory) -> None:
    conn = wired_factory["conn"]
    orch = wired_factory["orchestrator"]

    _insert_atom(conn, "wi_lease")
    conn.execute(
        """
        INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at, attempts, max_attempts, lease_owner, lease_until)
        VALUES ('wi_lease', ?, 10, strftime('%Y-%m-%dT%H:%M:%f','now'), 0, 3, 'stale-worker', strftime('%Y-%m-%dT%H:%M:%f','now','-2 hour'))
        """,
        (QueueName.FORGE_INBOX.value,),
    )
    conn.commit()

    orch._expire_leases()
    conn.commit()

    q = conn.execute("SELECT lease_owner, lease_until FROM work_item_queue WHERE work_item_id='wi_lease'").fetchone()
    assert q is not None
    assert q["lease_owner"] is None
    assert q["lease_until"] is None


def test_events_endpoint_sse_streams_event_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = tmp_path / "events_sse.db"
    monkeypatch.setenv("FACTORY_DB_PATH", str(db))
    conn = init_db(db)
    conn.execute(
        """
        INSERT INTO work_items (id, parent_id, root_id, kind, title, status, creator_role, owner_role)
        VALUES ('w1', NULL, 'w1', 'atom', 'A', 'draft', 'creator', 'creator')
        """
    )
    conn.execute(
        """
        INSERT INTO event_log (event_time, event_type, entity_type, entity_id, work_item_id, severity, message, payload)
        VALUES
            (strftime('%Y-%m-%dT%H:%M:%f','now'), 'task.created', 'work_item', 'w1', 'w1', 'info', 'created', '{}'),
            (strftime('%Y-%m-%dT%H:%M:%f','now'), 'task.updated', 'work_item', 'w1', 'w1', 'info', 'updated', '{}')
        """
    )
    conn.commit()
    conn.close()

    client = TestClient(app)
    with client.stream("GET", "/api/events?limit=2&stream=true") as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: task." in body
    assert "data: {" in body
    assert "\n\n" in body
