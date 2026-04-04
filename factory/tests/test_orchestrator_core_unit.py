from __future__ import annotations

from pathlib import Path

from factory.composition import wire
from factory.models import QueueName


def _insert_atom_in_queue(conn, wi_id: str, queue: str) -> None:
    now = "2026-03-30T12:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority,
            created_at, updated_at
        )
        VALUES (?, NULL, ?, 'atom', 'A', '', 'ready_for_work', 'creator', 'forge', 1, 1, ?, ?)
        """,
        (wi_id, wi_id, now, now),
    )
    conn.execute(
        """
        INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at, attempts, max_attempts)
        VALUES (?, ?, 10, ?, 0, 3)
        """,
        (wi_id, queue, now),
    )


def test_process_queue_claims_ready_item_and_runs_handler(tmp_path: Path) -> None:
    f = wire(tmp_path / "orch_claim.db")
    conn = f["conn"]
    orch = f["orchestrator"]
    try:
        _insert_atom_in_queue(conn, "wi_claim", QueueName.COMPLETION_INBOX.value)
        conn.commit()

        seen = {}

        def _handler(item: dict) -> None:
            row = conn.execute(
                "SELECT lease_owner FROM work_item_queue WHERE work_item_id = ?",
                (item["work_item_id"],),
            ).fetchone()
            seen["lease_owner"] = row["lease_owner"] if row else None

        orch._process_queue(QueueName.COMPLETION_INBOX, _handler)

        assert seen["lease_owner"] == "orchestrator_tick"
    finally:
        conn.close()


def test_process_queue_handler_error_increments_attempts_and_releases_lease(tmp_path: Path) -> None:
    f = wire(tmp_path / "orch_error.db")
    conn = f["conn"]
    orch = f["orchestrator"]
    try:
        _insert_atom_in_queue(conn, "wi_error", QueueName.COMPLETION_INBOX.value)
        conn.commit()

        def _boom(_item: dict) -> None:
            raise RuntimeError("handler failed")

        orch._process_queue(QueueName.COMPLETION_INBOX, _boom)

        row = conn.execute(
            """
            SELECT attempts, last_error, lease_owner, lease_until
            FROM work_item_queue
            WHERE work_item_id = ?
            """,
            ("wi_error",),
        ).fetchone()
        assert row["attempts"] == 3
        assert "handler failed" in (row["last_error"] or "")
        assert row["lease_owner"] is None
        assert row["lease_until"] is None
    finally:
        conn.close()


def test_start_idle_loop_calls_tick_and_sleeps(monkeypatch, tmp_path: Path) -> None:
    f = wire(tmp_path / "orch_idle.db")
    orch = f["orchestrator"]
    conn = f["conn"]
    try:
        calls = {"tick": 0, "sleep": []}

        def _tick_once() -> None:
            calls["tick"] += 1
            orch._running = False

        def _sleep(seconds: float) -> None:
            calls["sleep"].append(seconds)

        monkeypatch.setattr(orch, "tick", _tick_once)
        monkeypatch.setattr("factory.orchestrator_core.time.sleep", _sleep)

        orch.start()

        assert calls["tick"] == 1
        assert len(calls["sleep"]) == 1
        assert calls["sleep"][0] > 0
    finally:
        conn.close()
