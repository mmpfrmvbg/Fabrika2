from __future__ import annotations

from pathlib import Path
from typing import cast

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


def _insert_work_item_with_queue(
    conn,
    *,
    wi_id: str,
    queue: str,
    status: str,
    priority: int,
    available_at: str,
) -> None:
    now = "2026-03-30T12:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority,
            created_at, updated_at
        )
        VALUES (?, NULL, ?, 'atom', ?, '', ?, 'creator', 'forge', 1, 1, ?, ?)
        """,
        (wi_id, wi_id, wi_id, status, now, now),
    )
    conn.execute(
        """
        INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at, attempts, max_attempts)
        VALUES (?, ?, ?, ?, 0, 3)
        """,
        (wi_id, queue, priority, available_at),
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


def test_process_queue_prefers_higher_priority_then_earlier_available_at(tmp_path: Path) -> None:
    f = wire(tmp_path / "orch_order_completion.db")
    conn = f["conn"]
    orch = f["orchestrator"]
    seen: list[str] = []
    try:
        _insert_work_item_with_queue(
            conn,
            wi_id="wi_low",
            queue=QueueName.COMPLETION_INBOX.value,
            status="ready_for_work",
            priority=1,
            available_at="2026-03-30T11:59:00.000000Z",
        )
        _insert_work_item_with_queue(
            conn,
            wi_id="wi_high_late",
            queue=QueueName.COMPLETION_INBOX.value,
            status="ready_for_work",
            priority=10,
            available_at="2026-03-30T11:59:05.000000Z",
        )
        _insert_work_item_with_queue(
            conn,
            wi_id="wi_high_early",
            queue=QueueName.COMPLETION_INBOX.value,
            status="ready_for_work",
            priority=10,
            available_at="2026-03-30T11:59:01.000000Z",
        )
        conn.commit()

        def _record(item: dict) -> None:
            seen.append(item["work_item_id"])
            conn.execute(
                "DELETE FROM work_item_queue WHERE work_item_id = ?",
                (item["work_item_id"],),
            )

        orch._process_queue(QueueName.COMPLETION_INBOX, _record)

        assert seen == ["wi_high_early", "wi_high_late", "wi_low"]
    finally:
        conn.close()


def test_forge_and_review_queues_share_priority_ordering(monkeypatch, tmp_path: Path) -> None:
    f = wire(tmp_path / "orch_order_forge_review.db")
    conn = f["conn"]
    orch = f["orchestrator"]
    forge_seen: list[str] = []
    review_seen: list[str] = []
    try:
        _insert_work_item_with_queue(
            conn,
            wi_id="forge_low",
            queue=QueueName.FORGE_INBOX.value,
            status="ready_for_work",
            priority=2,
            available_at="2026-03-30T11:59:00.000000Z",
        )
        _insert_work_item_with_queue(
            conn,
            wi_id="forge_high",
            queue=QueueName.FORGE_INBOX.value,
            status="ready_for_work",
            priority=9,
            available_at="2026-03-30T11:59:00.000000Z",
        )
        _insert_work_item_with_queue(
            conn,
            wi_id="review_low",
            queue=QueueName.REVIEW_INBOX.value,
            status="in_review",
            priority=2,
            available_at="2026-03-30T11:59:00.000000Z",
        )
        _insert_work_item_with_queue(
            conn,
            wi_id="review_high",
            queue=QueueName.REVIEW_INBOX.value,
            status="in_review",
            priority=9,
            available_at="2026-03-30T11:59:00.000000Z",
        )
        conn.commit()

        def _apply_transition(wi_id: str, *_args, **_kwargs):
            forge_seen.append(wi_id)
            return True, "ok"

        monkeypatch.setattr(orch.sm, "apply_transition", _apply_transition)
        monkeypatch.setattr(
            "factory.orchestrator_core.forge.run_forge_queued_runs",
            lambda _orch: None,
        )

        def _review_handler(item: dict) -> None:
            review_seen.append(item["work_item_id"])
            conn.execute(
                "DELETE FROM work_item_queue WHERE work_item_id = ?",
                (item["work_item_id"],),
            )

        monkeypatch.setattr(orch, "_dispatch_reviewer", _review_handler)

        orch._dispatch_ready_atoms()
        orch.process_review_queue()

        assert forge_seen[:2] == ["forge_high", "forge_low"]
        assert review_seen[:2] == ["review_high", "review_low"]
    finally:
        conn.close()


def test_start_idle_loop_calls_tick_and_sleeps(monkeypatch, tmp_path: Path) -> None:
    f = wire(tmp_path / "orch_idle.db")
    orch = f["orchestrator"]
    conn = f["conn"]
    try:
        calls: dict[str, int | list[float]] = {"tick": 0, "sleep": []}

        def _tick_once() -> None:
            calls["tick"] = cast(int, calls["tick"]) + 1
            orch._running = False

        def _sleep(seconds: float) -> None:
            cast(list[float], calls["sleep"]).append(seconds)

        monkeypatch.setattr(orch, "tick", _tick_once)
        monkeypatch.setattr("factory.orchestrator_core.time.sleep", _sleep)

        orch.start()

        assert calls["tick"] == 1
        sleeps = cast(list[float], calls["sleep"])
        assert len(sleeps) == 1
        assert sleeps[0] > 0
    finally:
        conn.close()
