from __future__ import annotations

from pathlib import Path

from factory.composition import wire


def _insert_in_progress_atom(conn, wi_id: str, *, attempts: int, max_attempts: int) -> None:
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, retry_count, max_retries
        )
        VALUES (?, NULL, ?, 'atom', 'Dead-letter atom', '', 'in_progress',
                'creator', 'forge', 1, 1, ?, ?)
        """,
        (wi_id, wi_id, attempts, max_attempts),
    )
    conn.execute(
        """
        INSERT INTO work_item_queue (work_item_id, queue_name, attempts, max_attempts, lease_owner, lease_until)
        VALUES (?, 'forge_inbox', ?, ?, 'worker-1', strftime('%Y-%m-%dT%H:%M:%f','now','+10 minutes'))
        """,
        (wi_id, attempts, max_attempts),
    )


def test_forge_failed_moves_item_to_dead_on_last_allowed_retry(tmp_path: Path) -> None:
    factory = wire(tmp_path / "dead_letter_last_retry.db")
    conn = factory["conn"]
    sm = factory["sm"]
    try:
        _insert_in_progress_atom(conn, "wi_dead_1", attempts=2, max_attempts=3)
        conn.commit()

        ok, _msg = sm.apply_transition("wi_dead_1", "forge_failed", actor_role="forge")
        conn.commit()
        assert ok is True

        row = conn.execute(
            "SELECT status, dead_at FROM work_items WHERE id = 'wi_dead_1'"
        ).fetchone()
        assert row["status"] == "dead"
        assert row["dead_at"] is not None

        qrow = conn.execute(
            "SELECT 1 FROM work_item_queue WHERE work_item_id = 'wi_dead_1'"
        ).fetchone()
        assert qrow is None
    finally:
        conn.close()
