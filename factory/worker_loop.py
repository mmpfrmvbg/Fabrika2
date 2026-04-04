"""
Бесконечный цикл оркестратора: tick → пауза → снова.

Остановка: Ctrl+C (KeyboardInterrupt). Событие ``worker.idle`` при отсутствии задач в очередях.
"""

from __future__ import annotations

import os
import sqlite3
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


def cleanup_stale_locks(conn) -> int:
    """
    Очищает зависшие блокировки файлов (истёкшие более 5 минут назад).

    Возвращает количество освобождённых блокировок.
    """
    # Освобождаем блокировки, у которых expires_at истёк более 5 минут назад
    # Используем replace для корректного сравнения ISO дат с 'T'
    result = conn.execute(
        """
        UPDATE file_locks
        SET released_at = strftime('%Y-%m-%dT%H:%M:%f','now'),
            lock_reason = lock_reason || ' [auto-cleanup by worker_loop]'
        WHERE released_at IS NULL
          AND replace(expires_at, 'T', ' ') < datetime('now', '-5 minutes')
        """
    )
    # Снимаем lease с задач, у которых все блокировки освобождены
    conn.execute(
        """
        UPDATE work_item_queue
        SET lease_owner = NULL, lease_until = NULL
        WHERE work_item_id IN (
            SELECT fl.work_item_id
            FROM file_locks fl
            WHERE fl.released_at IS NOT NULL
              AND fl.lock_reason LIKE '%auto-cleanup%'
        )
        AND lease_owner IS NOT NULL
        """
    )
    # Переводим атомы из judge_rejected/in_progress в ready_for_work
    conn.execute(
        """
        UPDATE work_items
        SET status = 'ready_for_work',
            previous_status = status,
            updated_at = strftime('%Y-%m-%dT%H:%M:%f','now')
        WHERE id IN (
            SELECT DISTINCT fl.work_item_id
            FROM file_locks fl
            WHERE fl.released_at IS NOT NULL
              AND fl.lock_reason LIKE '%auto-cleanup%'
        )
        AND status IN ('judge_rejected', 'in_progress', 'in_review')
        AND kind = 'atom'
        """
    )
    return result.rowcount


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

    tick_count = 0
    checkpoint_interval = max(1, 60000 // poll_ms)  # WAL checkpoint каждые ~60 сек
    cleanup_interval = max(1, 300000 // poll_ms)  # Очистка блокировок каждые ~5 мин

    try:
        while True:
            # Периодическая очистка зависших блокировок
            if tick_count % cleanup_interval == 0:
                cleaned = cleanup_stale_locks(conn)
                if cleaned > 0:
                    logger.log(
                        EventType.WORKER_IDLE,
                        "system",
                        "worker",
                        f"auto-cleanup: освобождено {cleaned} блокировок",
                        severity=Severity.INFO,
                        actor_role=Role.ORCHESTRATOR.value,
                        payload={"sub": "stale_locks_cleanup", "cleaned_count": cleaned},
                        tags=["worker", "cleanup", "locks"],
                    )
                conn.commit()

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
            tick_count += 1

            # Периодический WAL checkpoint для предотвращения разрастания factory.db-wal
            if tick_count % checkpoint_interval == 0:
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.OperationalError:
                    pass  # Пропускаем, если БД заблокирована

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
