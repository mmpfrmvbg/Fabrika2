"""
Бесконечный цикл оркестратора: tick → пауза → снова.

Остановка: Ctrl+C (KeyboardInterrupt). Событие ``worker.idle`` при отсутствии задач в очередях.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from .composition import wire
from .config import resolve_db_path
from .models import EventType, Role, Severity


def factory_has_pending_dispatch(conn) -> bool:
    """Есть ли что-то, что оркестратор мог бы взять из очередей или forge ``queued``."""
    q = conn.execute(
        """
        SELECT 1 FROM work_item_queue wiq
        WHERE wiq.lease_owner IS NULL
          AND wiq.available_at <= strftime('%Y-%m-%dT%H:%M:%f','now')
          AND wiq.attempts < wiq.max_attempts
        LIMIT 1
        """
    ).fetchone()
    if q:
        return True
    r = conn.execute(
        """
        SELECT 1 FROM runs r
        JOIN work_items wi ON wi.id = r.work_item_id
        WHERE r.role = 'forge' AND r.run_type = 'implement'
          AND r.status = 'queued' AND wi.status = 'in_progress'
        LIMIT 1
        """
    ).fetchone()
    return r is not None


def run_worker_loop(db_path: Path | None = None) -> None:
    p = resolve_db_path(db_path)
    if not os.environ.get("FACTORY_WORKSPACE_ROOT"):
        os.environ["FACTORY_WORKSPACE_ROOT"] = str(p.parent)

    factory = wire(db_path)
    conn = factory["conn"]
    orch = factory["orchestrator"]
    logger = factory["logger"]

    poll_ms = max(50, int(os.environ.get("FACTORY_WORKER_POLL_MS", "2000")))
    print(
        f"worker-loop: FACTORY_WORKSPACE_ROOT={os.environ.get('FACTORY_WORKSPACE_ROOT')} "
        f"poll={poll_ms}ms DB={p} (Ctrl+C to stop)"
    )

    try:
        while True:
            if not factory_has_pending_dispatch(conn):
                logger.log(
                    EventType.WORKER_IDLE,
                    "system",
                    "worker",
                    "worker.idle — нет задач в очередях",
                    severity=Severity.INFO,
                    actor_role=Role.ORCHESTRATOR.value,
                    payload={"sub": "idle", "poll_ms": poll_ms},
                    tags=["worker", "idle"],
                )
                conn.commit()

            orch.tick()

            time.sleep(poll_ms / 1000.0)
    except KeyboardInterrupt:
        logger.log(
            EventType.WORKER_STOPPED,
            "system",
            "worker",
            "worker.stopped (Ctrl+C)",
            severity=Severity.INFO,
            actor_role=Role.ORCHESTRATOR.value,
            payload={"sub": "worker_stopped"},
            tags=["worker", "lifecycle"],
        )
        conn.commit()
        print("worker-loop: stopped.")


if __name__ == "__main__":
    from .config import load_dotenv

    load_dotenv()
    run_worker_loop()
