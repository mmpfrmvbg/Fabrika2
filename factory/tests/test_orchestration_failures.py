from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import factory.webhooks as webhooks
from factory.actions import Actions
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


def _insert_atom(
    conn: sqlite3.Connection,
    wi_id: str,
    *,
    status: str = "ready_for_work",
) -> None:
    now = "2026-03-30T12:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            previous_status, creator_role, owner_role,
            planning_depth, priority,
            created_at, updated_at
        )
        VALUES (
            ?, NULL, ?, 'atom', 'A', '', ?, 'ready_for_work',
            'creator', 'forge', 1, 10, ?, ?
        )
        """,
        (wi_id, wi_id, status, now, now),
    )


def test_worker_crash_startup_recovery_reenqueues_on_tick(
    wired_factory,
) -> None:
    conn = wired_factory["conn"]
    logger = FactoryLogger(conn)

    _insert_atom(conn, "wi_crash", status="running")
    conn.execute(
        """
        INSERT INTO work_item_queue (
            work_item_id, queue_name, priority, available_at,
            attempts, max_attempts, lease_owner, lease_until
        )
        VALUES (
            'wi_crash', ?, 10, strftime('%Y-%m-%dT%H:%M:%f', 'now'),
            0, 3, 'worker-1',
            strftime('%Y-%m-%dT%H:%M:%f', 'now', '+30 minute')
        )
        """,
        (QueueName.FORGE_INBOX.value,),
    )
    conn.commit()

    recovered = recover_stuck_running_work_items(
        conn, logger, worker_id="worker-startup"
    )
    conn.commit()

    assert recovered == 1
    wi = conn.execute(
        "SELECT status, previous_status FROM work_items WHERE id='wi_crash'"
    ).fetchone()
    q = conn.execute(
        "SELECT lease_owner, lease_until FROM work_item_queue "
        "WHERE work_item_id='wi_crash'"
    ).fetchone()
    assert wi["status"] == "ready_for_work"
    assert wi["previous_status"] == "running"
    assert q is not None
    assert q["lease_owner"] is None
    assert q["lease_until"] is None


def test_dead_letter_exhaustion_marks_item_dead(wired_factory) -> None:
    conn = wired_factory["conn"]
    orch = wired_factory["orchestrator"]

    _insert_atom(conn, "wi_dead")
    conn.execute(
        """
        INSERT INTO work_item_queue (
            work_item_id, queue_name, priority, available_at,
            attempts, max_attempts
        )
        VALUES ('wi_dead', ?, 10, strftime('%Y-%m-%dT%H:%M:%f','now'), 0, 3)
        """,
        (QueueName.COMPLETION_INBOX.value,),
    )
    conn.commit()

    def _always_fail(_item: dict) -> None:
        raise RuntimeError("boom")

    orch._process_queue(QueueName.COMPLETION_INBOX, _always_fail)
    conn.commit()

    row = conn.execute(
        "SELECT status, dead_at FROM work_items WHERE id='wi_dead'"
    ).fetchone()
    q = conn.execute(
        "SELECT attempts, lease_owner FROM work_item_queue "
        "WHERE work_item_id='wi_dead'"
    ).fetchone()
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
        INSERT INTO work_item_queue (
            work_item_id, queue_name, priority, available_at,
            attempts, max_attempts, lease_owner, lease_until
        )
        VALUES (
            'wi_lease', ?, 10, strftime('%Y-%m-%dT%H:%M:%f', 'now'),
            0, 3, 'stale-worker',
            strftime('%Y-%m-%dT%H:%M:%f', 'now', '-2 hour')
        )
        """,
        (QueueName.FORGE_INBOX.value,),
    )
    conn.commit()

    orch._expire_leases()
    conn.commit()

    q = conn.execute(
        "SELECT lease_owner, lease_until FROM work_item_queue "
        "WHERE work_item_id='wi_lease'"
    ).fetchone()
    assert q is not None
    assert q["lease_owner"] is None
    assert q["lease_until"] is None


def test_events_endpoint_sse_streams_event_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db = tmp_path / "events_sse.db"
    monkeypatch.setenv("FACTORY_DB_PATH", str(db))
    conn = init_db(db)
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, status,
            creator_role, owner_role
        )
        VALUES ('w1', NULL, 'w1', 'atom', 'A', 'draft', 'creator', 'creator')
        """
    )
    conn.execute(
        """
        INSERT INTO event_log (
            event_time, event_type, entity_type, entity_id,
            work_item_id, severity, message, payload
        )
        VALUES
            (
                strftime('%Y-%m-%dT%H:%M:%f', 'now'),
                'task.created', 'work_item', 'w1', 'w1',
                'info', 'created', '{}'
            ),
            (
                strftime('%Y-%m-%dT%H:%M:%f', 'now'),
                'task.updated', 'work_item', 'w1', 'w1',
                'info', 'updated', '{}'
            )
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


def test_action_increment_retry_sets_exponential_backoff_with_jitter(
    wired_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = wired_factory["conn"]
    actions = Actions(conn, wired_factory["logger"], wired_factory["accounts"])
    _insert_atom(conn, "wi_retry", status="in_progress")
    conn.execute(
        """
        INSERT INTO work_item_queue (
            work_item_id, queue_name, priority, available_at, attempts, max_attempts
        )
        VALUES ('wi_retry', ?, 10, strftime('%Y-%m-%dT%H:%M:%f','now'), 1, 5)
        """,
        (QueueName.FORGE_INBOX.value,),
    )
    conn.commit()
    monkeypatch.setattr("factory.actions.random.randint", lambda _a, _b: 7)

    actions.action_increment_retry("wi_retry")
    conn.commit()

    row = conn.execute(
        """
        SELECT attempts,
               ROUND((julianday(available_at) - julianday('now')) * 86400.0) AS delay_sec
        FROM work_item_queue
        WHERE work_item_id = 'wi_retry'
        """
    ).fetchone()
    assert row is not None
    assert row["attempts"] == 2
    assert 64 <= int(row["delay_sec"]) <= 69  # 30*2^1 + 7 == 67 sec (+/- SQL timing)


def test_action_increment_retry_promotes_to_dead_and_notifies_webhook(
    wired_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = wired_factory["conn"]
    actions = Actions(conn, wired_factory["logger"], wired_factory["accounts"])
    _insert_atom(conn, "wi_dead_retry", status="in_progress")
    conn.execute(
        """
        INSERT INTO work_item_queue (
            work_item_id, queue_name, priority, available_at, attempts, max_attempts
        )
        VALUES ('wi_dead_retry', ?, 10, strftime('%Y-%m-%dT%H:%M:%f','now'), 3, 3)
        """,
        (QueueName.FORGE_INBOX.value,),
    )
    sent: list[dict[str, str]] = []
    monkeypatch.setattr(webhooks, "send_webhook_async", lambda payload: sent.append(payload))

    actions.action_increment_retry("wi_dead_retry")
    conn.commit()

    row = conn.execute("SELECT status, dead_at FROM work_items WHERE id='wi_dead_retry'").fetchone()
    q = conn.execute("SELECT 1 FROM work_item_queue WHERE work_item_id='wi_dead_retry'").fetchone()
    assert row["status"] == "dead"
    assert row["dead_at"] is not None
    assert q is None
    assert sent and sent[-1]["event_type"] == "work_item.dead"


def test_tick_enforces_deadline_and_fires_webhook(
    wired_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = wired_factory["conn"]
    orch = wired_factory["orchestrator"]
    sent: list[dict[str, str]] = []
    monkeypatch.setattr(webhooks, "send_webhook_async", lambda payload: sent.append(payload))
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            previous_status, creator_role, owner_role, planning_depth, priority,
            deadline_at, created_at, updated_at
        )
        VALUES (
            'wi_deadline', NULL, 'wi_deadline', 'task', 'Deadline', '',
            'in_progress', 'ready_for_work', 'creator', 'forge', 0, 0,
            strftime('%Y-%m-%dT%H:%M:%f','now','-5 minutes'),
            strftime('%Y-%m-%dT%H:%M:%f','now','-10 minutes'),
            strftime('%Y-%m-%dT%H:%M:%f','now','-10 minutes')
        )
        """
    )
    conn.commit()

    orch.tick()
    conn.commit()

    row = conn.execute(
        "SELECT status, failure_reason FROM work_items WHERE id='wi_deadline'"
    ).fetchone()
    assert row is not None
    assert row["status"] == "failed"
    assert row["failure_reason"] == "deadline_exceeded"
    assert sent and sent[-1]["event_type"] == "work_item.deadline_exceeded"
