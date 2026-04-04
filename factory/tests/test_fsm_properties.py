from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from factory.composition import wire
from factory.db import init_db
from factory.models import QueueName
from factory.queue_ops import claim_forge_inbox_atom
from factory.worker import recover_stuck_running_work_items

TERMINAL_STATES = ("done", "failed", "cancelled")


def _insert_atom(conn: sqlite3.Connection, wi_id: str, *, status: str) -> None:
    now = "2026-03-30T12:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority,
            created_at, updated_at
        )
        VALUES (?, NULL, ?, 'atom', 'A', '', ?, 'creator', 'forge', 1, 1, ?, ?)
        """,
        (wi_id, wi_id, status, now, now),
    )


@given(st.sampled_from(["forge_completed", "forge_failed", "review_passed", "review_failed", "judge_approved", "judge_rejected"]))
def test_terminal_states_do_not_transition_to_non_terminal(tmp_path: Path, event_name: str) -> None:
    factory = wire(tmp_path / "fsm_props_terminal.db")
    conn = factory["conn"]
    try:
        for terminal in TERMINAL_STATES:
            wi_id = f"wi_{terminal}"
            _insert_atom(conn, wi_id, status=terminal)
            wi = conn.execute("SELECT * FROM work_items WHERE id = ?", (wi_id,)).fetchone()
            rule = factory["sm"].find_matching_transition(wi, event_name)
            if rule is not None:
                assert rule["to_state"] in TERMINAL_STATES
    finally:
        conn.close()


@given(st.sampled_from([None, "", "draft", "ready_for_work", "in_progress"]))
def test_recovery_never_returns_pending_after_processing_started(tmp_path: Path, previous_status: str | None) -> None:
    factory = wire(tmp_path / "fsm_props_recovery.db")
    conn = factory["conn"]
    logger = factory["logger"]
    try:
        _insert_atom(conn, "wi_running", status="running")
        conn.execute(
            "UPDATE work_items SET previous_status = ? WHERE id = 'wi_running'",
            (previous_status,),
        )
        conn.execute(
            """
            INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at, lease_owner, lease_until)
            VALUES ('wi_running', ?, 10, strftime('%Y-%m-%dT%H:%M:%f','now'), 'worker-1', strftime('%Y-%m-%dT%H:%M:%f','now','+30 minute'))
            """,
            (QueueName.FORGE_INBOX.value,),
        )
        conn.commit()

        recover_stuck_running_work_items(conn, logger, worker_id="worker-prop")
        conn.commit()

        row = conn.execute("SELECT status FROM work_items WHERE id='wi_running'").fetchone()
        assert row is not None
        assert row["status"] in ("in_progress", *TERMINAL_STATES, "ready_for_work")
        assert row["status"] != "pending"
    finally:
        conn.close()


@given(st.sampled_from(["worker-a", "worker-b", "worker-c"]))
def test_concurrent_claims_have_single_lease_holder(tmp_path: Path, first_worker: str) -> None:
    db_path = tmp_path / "fsm_props_claims.db"
    conn = init_db(db_path)
    try:
        _insert_atom(conn, "wi_claim", status="ready_for_work")
        conn.execute(
            "INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at) VALUES ('wi_claim', ?, 10, strftime('%Y-%m-%dT%H:%M:%f','now'))",
            (QueueName.FORGE_INBOX.value,),
        )
        conn.commit()
    finally:
        conn.close()

    claims: list[str | None] = []
    lock = threading.Lock()

    def _claim(worker_id: str) -> None:
        c = sqlite3.connect(str(db_path), timeout=3.0, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout = 3000")
        try:
            res = claim_forge_inbox_atom(c, worker_id)
            c.commit()
            with lock:
                claims.append(res)
        finally:
            c.close()

    second_worker = "worker-z" if first_worker != "worker-z" else "worker-y"
    t1 = threading.Thread(target=_claim, args=(first_worker,))
    t2 = threading.Thread(target=_claim, args=(second_worker,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sum(1 for item in claims if item == "wi_claim") == 1

    verify = sqlite3.connect(str(db_path))
    verify.row_factory = sqlite3.Row
    try:
        row = verify.execute(
            "SELECT lease_owner FROM work_item_queue WHERE work_item_id='wi_claim'"
        ).fetchone()
        assert row is not None
        assert row["lease_owner"] in (first_worker, second_worker)
    finally:
        verify.close()
