from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import Mock, patch

from factory.worker_loop import cleanup_stale_locks, factory_has_pending_dispatch


def _make_dispatch_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE work_item_queue (
            work_item_id TEXT,
            lease_owner TEXT,
            available_at TEXT,
            attempts INTEGER,
            max_attempts INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE work_items (
            id TEXT PRIMARY KEY,
            status TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_item_id TEXT,
            role TEXT,
            run_type TEXT,
            status TEXT
        )
        """
    )
    return conn


def _make_cleanup_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE file_locks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_item_id TEXT,
            expires_at TEXT,
            released_at TEXT,
            lock_reason TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE work_item_queue (
            work_item_id TEXT,
            lease_owner TEXT,
            lease_until TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE work_items (
            id TEXT PRIMARY KEY,
            status TEXT,
            previous_status TEXT,
            updated_at TEXT,
            kind TEXT
        )
        """
    )
    return conn


def test_factory_has_pending_dispatch_returns_false_for_empty_tables() -> None:
    conn = _make_dispatch_conn()

    assert factory_has_pending_dispatch(conn) is False


def test_factory_has_pending_dispatch_returns_true_when_queue_has_dispatchable_item() -> None:
    conn = _make_dispatch_conn()
    conn.execute(
        """
        INSERT INTO work_item_queue (work_item_id, lease_owner, available_at, attempts, max_attempts)
        VALUES ('wi1', NULL, strftime('%Y-%m-%dT%H:%M:%f','now'), 0, 3)
        """
    )

    assert factory_has_pending_dispatch(conn) is True


def test_cleanup_stale_locks_returns_zero_when_no_expired_locks() -> None:
    conn = _make_cleanup_conn()
    conn.execute(
        """
        INSERT INTO file_locks (work_item_id, expires_at, released_at, lock_reason)
        VALUES ('wi1', datetime('now', '+10 minutes'), NULL, 'active lock')
        """
    )

    cleaned = cleanup_stale_locks(conn)

    assert cleaned == 0


def test_cleanup_stale_locks_releases_expired_lock_and_resets_related_state() -> None:
    conn = _make_cleanup_conn()
    conn.execute(
        """
        INSERT INTO file_locks (work_item_id, expires_at, released_at, lock_reason)
        VALUES ('wi_stale', datetime('now', '-10 minutes'), NULL, 'stale lock')
        """
    )
    conn.execute(
        """
        INSERT INTO work_item_queue (work_item_id, lease_owner, lease_until)
        VALUES ('wi_stale', 'worker-1', datetime('now', '+5 minutes'))
        """
    )
    conn.execute(
        """
        INSERT INTO work_items (id, status, previous_status, updated_at, kind)
        VALUES ('wi_stale', 'in_progress', NULL, strftime('%Y-%m-%dT%H:%M:%f','now'), 'atom')
        """
    )

    cleaned = cleanup_stale_locks(conn)

    assert cleaned == 1

    lock_row = conn.execute(
        "SELECT released_at, lock_reason FROM file_locks WHERE work_item_id = 'wi_stale'"
    ).fetchone()
    assert lock_row is not None
    assert lock_row["released_at"] is not None
    assert "auto-cleanup by worker_loop" in lock_row["lock_reason"]

    queue_row = conn.execute(
        "SELECT lease_owner, lease_until FROM work_item_queue WHERE work_item_id = 'wi_stale'"
    ).fetchone()
    assert queue_row is not None
    assert queue_row["lease_owner"] is None
    assert queue_row["lease_until"] is None

    item_row = conn.execute(
        "SELECT status, previous_status FROM work_items WHERE id = 'wi_stale'"
    ).fetchone()
    assert item_row is not None
    assert item_row["status"] == "ready_for_work"
    assert item_row["previous_status"] == "in_progress"


def test_run_worker_loop_logs_idle_and_stops_on_keyboard_interrupt() -> None:
    conn = Mock()
    orch = Mock()
    logger = Mock()
    orch.tick.side_effect = KeyboardInterrupt

    with patch("factory.worker_loop.resolve_db_path", return_value=Path("/tmp/factory.db")), patch(
        "factory.worker_loop.wire",
        return_value={"conn": conn, "orchestrator": orch, "logger": logger},
    ), patch("factory.worker_loop.cleanup_stale_locks", return_value=0), patch(
        "factory.worker_loop.factory_has_pending_dispatch", return_value=False
    ), patch(
        "factory.worker_loop.time.sleep"
    ):
        from factory.worker_loop import run_worker_loop

        run_worker_loop()

    assert logger.log.call_count == 2
    idle_call = logger.log.call_args_list[0]
    assert idle_call.args[3] == "worker.idle — нет задач в очередях"
    stopped_call = logger.log.call_args_list[1]
    assert stopped_call.args[3] == "worker.stopped (Ctrl+C)"
    assert conn.commit.call_count == 3


def test_run_worker_loop_handles_wal_checkpoint_operational_error() -> None:
    conn = Mock()
    orch = Mock()
    logger = Mock()
    conn.execute.side_effect = sqlite3.OperationalError("busy")
    orch.tick.side_effect = [None, KeyboardInterrupt]

    with patch.dict("os.environ", {"FACTORY_WORKER_POLL_MS": "60000"}, clear=False), patch(
        "factory.worker_loop.resolve_db_path", return_value=Path("/tmp/factory.db")
    ), patch(
        "factory.worker_loop.wire",
        return_value={"conn": conn, "orchestrator": orch, "logger": logger},
    ), patch(
        "factory.worker_loop.cleanup_stale_locks", return_value=0
    ), patch(
        "factory.worker_loop.factory_has_pending_dispatch", return_value=True
    ), patch(
        "factory.worker_loop.time.sleep"
    ):
        from factory.worker_loop import run_worker_loop

        run_worker_loop()

    conn.execute.assert_called_once_with("PRAGMA wal_checkpoint(TRUNCATE)")
    assert orch.tick.call_count == 2
    assert logger.log.call_count == 1
    assert logger.log.call_args.args[3] == "worker.stopped (Ctrl+C)"
    assert conn.commit.call_count == 2


def test_run_worker_loop_emits_cleanup_event_when_stale_locks_released() -> None:
    conn = Mock()
    orch = Mock()
    logger = Mock()
    orch.tick.side_effect = KeyboardInterrupt

    with patch("factory.worker_loop.resolve_db_path", return_value=Path("/tmp/factory.db")), patch(
        "factory.worker_loop.wire",
        return_value={"conn": conn, "orchestrator": orch, "logger": logger},
    ), patch("factory.worker_loop.cleanup_stale_locks", return_value=2), patch(
        "factory.worker_loop.factory_has_pending_dispatch", return_value=True
    ), patch(
        "factory.worker_loop.time.sleep"
    ):
        from factory.worker_loop import run_worker_loop

        run_worker_loop()

    assert logger.log.call_count == 2
    assert logger.log.call_args_list[0].args[3] == "auto-cleanup: освобождено 2 блокировок"
    assert logger.log.call_args_list[1].args[3] == "worker.stopped (Ctrl+C)"
    assert conn.commit.call_count == 2
