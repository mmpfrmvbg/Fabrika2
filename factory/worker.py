"""
Отдельный процесс-исполнитель: claim ``forge_inbox`` → ``forge_started`` → forge → review → judge.

Не запускает цикл ``Orchestrator.tick()`` целиком; оркестратор в ``api_server`` по-прежнему обслуживает очереди.

Запуск: ``python -m factory.worker`` или ``python -m factory.worker --id worker-2 --poll 3``.
"""

from __future__ import annotations

import argparse
import os
import signal
import sqlite3
import sys
import time
from typing import Any

from .agents import forge
from .composition import wire
from .config import resolve_db_path
from .logging import FactoryLogger
from .models import EventType, Role, Severity
from .queue_ops import claim_forge_inbox_atom, release_queue_lease
from .worker_pipeline import drain_atom_downstream


def _env_poll_sec() -> float:
    raw = (os.environ.get("FACTORY_WORKER_POLL") or "5").strip()
    try:
        v = float(raw)
        return max(0.5, v)
    except ValueError:
        return 5.0


def _env_worker_id() -> str:
    return (os.environ.get("FACTORY_WORKER_ID") or "worker-1").strip() or "worker-1"


def _retry_backoff(attempt: int) -> float:
    return (1.0, 2.0, 4.0)[min(attempt, 2)]


def worker_iteration(factory: dict[str, Any], worker_id: str) -> bool:
    """
    Одна попытка взять атом и прогнать пайплайн. Возвращает True, если была работа.
    """
    orch = factory["orchestrator"]
    conn = factory["conn"]
    logger: FactoryLogger = factory["logger"]

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

        conn.commit()
        forge.run_forge_queued_runs(orch)
        conn.commit()
        drain_atom_downstream(orch, wi_id)
        conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.log(
            EventType.TASK_STATUS_CHANGED,
            "work_item",
            wi_id,
            f"worker iteration error: {e}",
            severity=Severity.ERROR,
            work_item_id=wi_id,
            payload={"sub": "worker_iteration_error", "error": str(e)},
        )
        try:
            release_queue_lease(conn, wi_id)
            conn.commit()
        except Exception:
            conn.rollback()
        return True


def _apply_worker_sqlite_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")


def run_worker_loop(*, worker_id: str, poll_sec: float) -> None:
    """Бесконечный цикл (до SIGINT/SIGTERM)."""
    os.environ.setdefault("FACTORY_ORCHESTRATOR_ASYNC", "0")

    stop = False

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
    conn.commit()

    try:
        while not stop:
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
            if not worked and not stop:
                time.sleep(poll_sec)
    finally:
        try:
            logger.log(
                EventType.TASK_STATUS_CHANGED,
                "system",
                "worker",
                f"Worker stopping id={worker_id}",
                actor_role=Role.ORCHESTRATOR.value,
                payload={"sub": "worker_stopped", "worker_id": worker_id},
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
