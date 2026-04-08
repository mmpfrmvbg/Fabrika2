"""
Отдельный процесс-исполнитель: claim ``forge_inbox`` → ``forge_started`` → forge → review → judge.

Не запускает цикл ``Orchestrator.tick()`` целиком; оркестратор в ``api_server`` по-прежнему обслуживает очереди.

Запуск: ``python -m factory.worker`` или ``python -m factory.worker --id worker-2 --poll 3``.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import signal
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .agents import forge
from .composition import wire
from .config import (
    SQLITE_BUSY_TIMEOUT_MS,
    SQLITE_TIMEOUT_SECONDS,
    WORKER_POLL_SECONDS,
    WORKER_STUCK_TIMEOUT_SECONDS,
    resolve_db_path,
)
from .logging import FactoryLogger
from .models import EventType, Role, Severity
from .queue_ops import claim_forge_inbox_atom, release_queue_lease
from .worker_pipeline import drain_atom_downstream
from .webhooks import notify_stuck

_HEARTBEAT_INTERVAL_SEC = 30.0
_STUCK_WORK_ITEM_TIMEOUT_SEC = WORKER_STUCK_TIMEOUT_SECONDS


def _env_poll_sec() -> float:
    raw = (os.environ.get("FACTORY_WORKER_POLL") or "").strip()
    if not raw:
        return WORKER_POLL_SECONDS
    try:
        return max(0.5, float(raw))
    except ValueError:
        return WORKER_POLL_SECONDS


def _env_worker_id() -> str:
    return (os.environ.get("FACTORY_WORKER_ID") or "worker-1").strip() or "worker-1"


def _retry_backoff(attempt: int) -> float:
    return (1.0, 2.0, 4.0)[min(attempt, 2)]


def _touch_work_item_heartbeat(conn: sqlite3.Connection, work_item_id: str) -> None:
    conn.execute(
        """
        UPDATE work_items
        SET last_heartbeat_at = strftime('%Y-%m-%dT%H:%M:%f','now')
        WHERE id = ?
        """,
        (work_item_id,),
    )


@contextlib.contextmanager
def _heartbeat_loop(db_path: Path, work_item_id: str):
    stop = threading.Event()
    hb_conn = sqlite3.connect(str(db_path), timeout=SQLITE_TIMEOUT_SECONDS, check_same_thread=False)
    hb_conn.row_factory = sqlite3.Row
    hb_conn.execute("PRAGMA journal_mode = WAL")
    hb_conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")

    def _runner() -> None:
        try:
            while not stop.wait(_HEARTBEAT_INTERVAL_SEC):
                _touch_work_item_heartbeat(hb_conn, work_item_id)
                hb_conn.commit()
        except sqlite3.Error:
            pass

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=1.0)
        hb_conn.close()


def recover_stuck_running_work_items(
    conn: sqlite3.Connection,
    logger: FactoryLogger,
    *,
    worker_id: str,
) -> int:
    timeout_seconds = max(1, int(_STUCK_WORK_ITEM_TIMEOUT_SEC))
    cutoff_expr = f"-{timeout_seconds} seconds"
    rows = conn.execute(
        """
        UPDATE work_items
        SET status = COALESCE(NULLIF(previous_status, ''), 'ready_for_work'),
            previous_status = status,
            last_heartbeat_at = NULL,
            updated_at = strftime('%Y-%m-%dT%H:%M:%f','now')
        WHERE status = 'running'
          AND (
                last_heartbeat_at IS NULL
                OR replace(last_heartbeat_at, 'T', ' ') < datetime('now', ?)
              )
        RETURNING id, status, previous_status, last_heartbeat_at
        """,
        (cutoff_expr,),
    ).fetchall()
    for row in rows:
        conn.execute(
            """
            UPDATE work_item_queue
            SET lease_owner = NULL,
                lease_until = NULL,
                available_at = strftime('%Y-%m-%dT%H:%M:%f','now')
            WHERE work_item_id = ?
            """,
            (row["id"],),
        )
    for row in rows:
        notify_stuck(
            work_item_id=row["id"],
            title=None,
            status=row["status"],
        )
        logger.log(
            EventType.WORK_ITEM_RECOVERED,
            "work_item",
            row["id"],
            "Recovered stuck running work item on worker startup",
            work_item_id=row["id"],
            actor_role=Role.ORCHESTRATOR.value,
            payload={
                "sub": "worker_startup_recovery",
                "worker_id": worker_id,
                "from_status": "running",
                "to_status": row["status"],
                "previous_status": row["previous_status"],
                "last_heartbeat_at": row["last_heartbeat_at"],
                "timeout_seconds": int(_STUCK_WORK_ITEM_TIMEOUT_SEC),
            },
            tags=["worker", "recovery"],
        )
    return len(rows)


def worker_iteration(factory: dict[str, Any], worker_id: str) -> bool:
    """
    Одна попытка взять атом и прогнать пайплайн. Возвращает True, если была работа.
    """
    orch = factory["orchestrator"]
    conn = factory["conn"]
    logger: FactoryLogger = factory["logger"]
    db_path = Path(factory.get("db_path") or resolve_db_path())

    _apply_worker_sqlite_pragmas(conn)
    try:
        wi_id = claim_forge_inbox_atom(conn, worker_id)
    except sqlite3.OperationalError as e:
        if "locked" not in str(e).lower():
            raise
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    if not wi_id:
        orphan = conn.execute(
            """
            SELECT r.work_item_id
            FROM runs r
            JOIN work_items wi ON wi.id = r.work_item_id
            WHERE r.role = 'forge'
              AND r.run_type = 'implement'
              AND r.status = 'queued'
              AND wi.status = 'in_progress'
            ORDER BY r.started_at ASC
            LIMIT 1
            """
        ).fetchone()
        if orphan:
            orphan_wi_id = orphan["work_item_id"]
            _touch_work_item_heartbeat(conn, orphan_wi_id)
            conn.commit()
            with _heartbeat_loop(db_path, orphan_wi_id):
                forge.run_forge_queued_runs(orch)
                conn.commit()
                drain_atom_downstream(orch, orphan_wi_id)
            conn.commit()
            return True
        return False

    try:
        ok, msg = orch.sm.apply_transition(
            wi_id,
            "forge_started",
            actor_role=Role.ORCHESTRATOR.value,
        )
        if not ok:
            logger.log(
                EventType.FORGE_FAILED,
                "work_item",
                wi_id,
                f"worker: forge_started failed: {msg}",
                severity=Severity.ERROR,
                work_item_id=wi_id,
                payload={"sub": "worker_forge_started", "error": msg},
            )
            release_queue_lease(conn, wi_id)
            conn.commit()
            return True

        _touch_work_item_heartbeat(conn, wi_id)
        conn.commit()
        with _heartbeat_loop(db_path, wi_id):
            forge.run_forge_queued_runs(orch)
            conn.commit()
            drain_atom_downstream(orch, wi_id)
        conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        failed_wi_id = wi_id
        if isinstance(e, forge.ForgeBatchRunError):
            failed_wi_id = e.work_item_id
        logger.log(
            EventType.TASK_STATUS_CHANGED,
            "work_item",
            failed_wi_id,
            f"worker iteration error: {e}",
            severity=Severity.ERROR,
            work_item_id=failed_wi_id,
            payload={
                "sub": "worker_iteration_error",
                "error": str(e),
                "claimed_work_item_id": wi_id,
                "failed_work_item_id": failed_wi_id,
            },
        )
        try:
            ok, msg = orch.sm.apply_transition(
                failed_wi_id,
                "forge_failed",
                actor_role=Role.ORCHESTRATOR.value,
            )
            if not ok:
                logger.log(
                    EventType.FORGE_FAILED,
                    "work_item",
                    failed_wi_id,
                    f"worker: forge_failed transition denied: {msg}",
                    severity=Severity.ERROR,
                    work_item_id=failed_wi_id,
                    payload={"sub": "worker_forge_failed_denied", "error": msg},
                )
            release_queue_lease(conn, failed_wi_id)
            conn.commit()
        except Exception:
            conn.rollback()
        return True


def _apply_worker_sqlite_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")


def run_worker_loop(*, worker_id: str, poll_sec: float) -> None:
    """Бесконечный цикл (до SIGINT/SIGTERM)."""
    os.environ.setdefault("FACTORY_ORCHESTRATOR_ASYNC", "0")

    stop = False
    shutting_down_logged = False

    def _handle_sig(_sig, _frame) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handle_sig)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_sig)

    db_path = resolve_db_path()
    factory = wire(db_path)
    conn = factory["conn"]
    _apply_worker_sqlite_pragmas(conn)
    logger = factory["logger"]
    logger.log(
        EventType.TASK_STATUS_CHANGED,
        "system",
        "worker",
        f"Worker started id={worker_id} poll={poll_sec}s db={db_path}",
        actor_role=Role.ORCHESTRATOR.value,
        payload={"sub": "worker_started", "worker_id": worker_id},
        tags=["worker", "lifecycle"],
    )
    recovered = recover_stuck_running_work_items(conn, logger, worker_id=worker_id)
    if recovered:
        logger.log(
            EventType.TASK_STATUS_CHANGED,
            "system",
            "worker",
            f"Recovered {recovered} stuck running work items",
            actor_role=Role.ORCHESTRATOR.value,
            payload={
                "sub": "worker_startup_recovery",
                "worker_id": worker_id,
                "recovered_count": recovered,
                "stuck_timeout_seconds": int(_STUCK_WORK_ITEM_TIMEOUT_SEC),
            },
            tags=["worker", "recovery"],
        )
    conn.commit()

    try:
        while True:
            worked = False
            for attempt in range(3):
                try:
                    worked = worker_iteration(factory, worker_id)
                    break
                except sqlite3.OperationalError as e:
                    if "locked" not in str(e).lower():
                        raise
                    time.sleep(_retry_backoff(attempt))
                except Exception as e:  # noqa: BLE001
                    logger.log(
                        EventType.TASK_STATUS_CHANGED,
                        "system",
                        "worker",
                        f"worker error: {e}",
                        severity=Severity.ERROR,
                        payload={"sub": "worker_loop_error", "error": str(e)},
                    )
                    conn.rollback()
                    break
            if stop and worked and not shutting_down_logged:
                logger.log(
                    EventType.TASK_STATUS_CHANGED,
                    "system",
                    "worker",
                    "Worker shutting down gracefully, finishing current item...",
                    actor_role=Role.ORCHESTRATOR.value,
                    payload={"sub": "worker_shutdown_graceful", "worker_id": worker_id},
                    tags=["worker", "lifecycle"],
                )
                conn.commit()
                shutting_down_logged = True
            if stop and not worked:
                break
            if not worked:
                time.sleep(poll_sec)
    finally:
        try:
            logger.log(
                EventType.TASK_STATUS_CHANGED,
                "system",
                "worker",
                "Worker stopped cleanly",
                actor_role=Role.ORCHESTRATOR.value,
                payload={"sub": "worker_stopped_cleanly", "worker_id": worker_id},
                tags=["worker", "lifecycle"],
            )
            conn.commit()
        except Exception:
            pass
        conn.close()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Factory forge worker (separate process)")
    ap.add_argument("--id", dest="worker_id", default=None, help="Worker id (or FACTORY_WORKER_ID)")
    ap.add_argument("--poll", type=float, default=None, help="Idle sleep seconds (or FACTORY_WORKER_POLL)")
    ns = ap.parse_args(argv)

    wid = (ns.worker_id or _env_worker_id()).strip()
    poll = float(ns.poll) if ns.poll is not None else _env_poll_sec()

    os.environ["FACTORY_WORKER_ID"] = wid
    os.environ["FACTORY_WORKER_POLL"] = str(poll)

    run_worker_loop(worker_id=wid, poll_sec=poll)


if __name__ == "__main__":
    main(sys.argv[1:])
