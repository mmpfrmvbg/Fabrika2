"""Детерминированный цикл опроса очередей: lease, разблокировки, FSM-события."""
import os
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import Callable

import sqlite3

from .config import AccountExhaustedError, AccountManager, resolve_db_path
from .config import (
    MAX_CONCURRENT_FORGE_RUNS,
    MAX_CONCURRENT_REVIEW_RUNS,
    ORCHESTRATOR_ARCHITECT_SCAN_TICKS,
    ORCHESTRATOR_POLL_INTERVAL,
)
from .agents import architect, forge, judge, planner, reviewer
from .fsm import StateMachine
from .logging import FactoryLogger
from .models import EventType, QueueName, Role, RunType, Severity

_forge_worker_lock = threading.Lock()
_active_forge_worker: threading.Thread | None = None

_review_worker_lock = threading.Lock()
_active_review_worker: threading.Thread | None = None


def _env_orchestrator_async() -> bool:
    return os.environ.get("FACTORY_ORCHESTRATOR_ASYNC", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def wait_for_async_workers(timeout: float = 120.0) -> None:
    """
    Ждёт завершения фоновых forge/review-воркеров (см. ``FACTORY_ORCHESTRATOR_ASYNC=1``).
    Для тестов и E2E после ``tick()`` / ``orchestrator_tick()``.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _forge_worker_lock:
            f = _active_forge_worker
        with _review_worker_lock:
            r = _active_review_worker
        if not ((f and f.is_alive()) or (r and r.is_alive())):
            break
        time.sleep(0.05)
    rem = max(0.0, deadline - time.monotonic())
    with _forge_worker_lock:
        f = _active_forge_worker
    with _review_worker_lock:
        r = _active_review_worker
    if f and f.is_alive():
        f.join(timeout=rem)
    if r and r.is_alive():
        r.join(timeout=rem)


class Orchestrator:
    """
    Детерминированный диспетчер фабрики (не LLM).

    Тик: housekeeping -> разблокировки -> completion_inbox -> диспетчеры очередей.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        sm: StateMachine,
        accounts: AccountManager,
        logger: FactoryLogger,
    ):
        self.conn = conn
        self.sm = sm
        self.accounts = accounts
        self.logger = logger
        self._running = False
        self._tick_counter = 0

    def start(self) -> None:
        self._running = True
        self._set_factory_status("running")
        self.logger.log(
            EventType.TASK_STATUS_CHANGED,
            "system",
            "orchestrator",
            "Оркестратор запущен",
            actor_role=Role.ORCHESTRATOR.value,
            tags=["lifecycle"],
            payload={"sub": "orchestrator_started"},
        )

        while self._running:
            try:
                self.tick()
            except AccountExhaustedError as e:
                self._handle_exhaustion(e)
            except Exception as e:
                self.logger.log(
                    EventType.TASK_STATUS_CHANGED,
                    "system",
                    "orchestrator",
                    f"Ошибка в цикле: {e}",
                    severity=Severity.ERROR,
                    payload={"error": str(e), "sub": "orchestrator_error"},
                )
            time.sleep(ORCHESTRATOR_POLL_INTERVAL)

    def stop(self) -> None:
        self._running = False
        self._set_factory_status("stopped")
        self.logger.log(
            EventType.TASK_STATUS_CHANGED,
            "system",
            "orchestrator",
            "Оркестратор остановлен",
            actor_role=Role.ORCHESTRATOR.value,
            tags=["lifecycle"],
            payload={"sub": "orchestrator_stopped"},
        )

    def tick(self) -> None:
        self._tick_counter += 1
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE system_state SET value = ? WHERE key = 'last_poll_at'",
            (now,),
        )

        self.accounts.get_active_account()

        self._expire_leases()
        self._check_blocked_items()

        self._auto_enqueue_ready_atoms()

        self._process_queue(QueueName.COMPLETION_INBOX, self._dispatch_completion)

        self._dispatch_ready_atoms()
        self.process_forge_queue()
        self.process_review_queue()
        # st_16 sent_to_judge: не внутри Reviewer — оркестратор (после review_failed → review_rejected).
        self._escalate_review_rejected_to_judge()
        self._process_queue(QueueName.JUDGE_INBOX, self._dispatch_judge)
        self._process_queue(QueueName.ARCHITECT_INBOX, self._dispatch_architect)
        self._process_queue(QueueName.PLANNER_INBOX, self._dispatch_planner)

        self._maybe_architect_proactive_scan()

        # Introspect pass — every N ticks (FACTORY_INTROSPECT_TICKS, default 20)
        try:
            from .factory_introspect import run_introspect_tick

            run_introspect_tick(self.conn, self.logger, tick_counter=self._tick_counter)
        except Exception as e:  # noqa: BLE001
            self.logger.log(
                EventType.TASK_STATUS_CHANGED,
                "system",
                "orchestrator",
                f"Introspect failed: {e}",
                severity=Severity.ERROR,
                actor_role=Role.ORCHESTRATOR.value,
                payload={"sub": "introspect_error", "error": str(e), "traceback": traceback.format_exc()},
            )

        self.conn.commit()

    def _process_queue(self, queue: QueueName, handler: Callable):
        """
        Один SQL ``UPDATE … WHERE rowid=(SELECT … LIMIT 1)`` атомарно выдаёт lease
        одному воркеру; гонка SELECT→UPDATE между процессами исключена.
        """
        qname = queue.value if isinstance(queue, QueueName) else str(queue)
        if qname not in {q.value for q in QueueName}:
            return
        for _ in range(5):
            cur = self.conn.execute(
                f"""
                UPDATE work_item_queue
                SET lease_owner = 'orchestrator_tick',
                    lease_until = strftime('%Y-%m-%dT%H:%M:%f','now','+30 minutes')
                WHERE rowid = (
                    SELECT wiq.rowid FROM work_item_queue wiq
                    INNER JOIN work_items wi ON wiq.work_item_id = wi.id
                    WHERE wiq.queue_name = '{qname}'
                      AND wiq.lease_owner IS NULL
                      AND wiq.available_at <= strftime('%Y-%m-%dT%H:%M:%f','now')
                      AND wiq.attempts < wiq.max_attempts
                    ORDER BY wiq.priority ASC
                    LIMIT 1
                )
                RETURNING work_item_id
                """,
            )
            claimed = cur.fetchone()
            if not claimed:
                break

            wi_id = claimed["work_item_id"]
            row = self.conn.execute(
                """
                SELECT wiq.*, wi.kind, wi.status, wi.title
                FROM work_item_queue wiq
                JOIN work_items wi ON wiq.work_item_id = wi.id
                WHERE wiq.work_item_id = ?
                """,
                (wi_id,),
            ).fetchone()
            if not row:
                self.conn.execute(
                    """
                    UPDATE work_item_queue
                    SET lease_owner = NULL, lease_until = NULL
                    WHERE work_item_id = ?
                    """,
                    (wi_id,),
                )
                continue

            item = dict(row)
            self.logger.log(
                EventType.TASK_DEQUEUED,
                "work_item",
                item["work_item_id"],
                f"task.dequeued {queue.value}",
                work_item_id=item["work_item_id"],
                payload={"queue_name": queue.value},
                tags=["queue", queue.value],
            )
            try:
                handler(dict(item))
            except AccountExhaustedError:
                raise
            except Exception as e:
                self.logger.log(
                    EventType.TASK_STATUS_CHANGED,
                    "work_item",
                    item["work_item_id"],
                    f"Ошибка обработки: {e}",
                    severity=Severity.ERROR,
                    work_item_id=item["work_item_id"],
                    payload={"queue": queue.value, "error": str(e), "sub": "queue_handler_error"},
                )
                self.conn.execute(
                    """
                    UPDATE work_item_queue
                    SET attempts = attempts + 1,
                        last_error = ?,
                        lease_owner = NULL,
                        lease_until = NULL
                    WHERE work_item_id = ?
                    """,
                    (str(e), item["work_item_id"]),
                )
                exhausted = self.conn.execute(
                    """
                    SELECT attempts, max_attempts
                    FROM work_item_queue
                    WHERE work_item_id = ?
                    """,
                    (item["work_item_id"],),
                ).fetchone()
                if (
                    qname == QueueName.COMPLETION_INBOX.value
                    and exhausted
                    and int(exhausted["attempts"] or 0) >= int(exhausted["max_attempts"] or 0)
                ):
                    self.conn.execute(
                        """
                        UPDATE work_items
                        SET status = 'dead',
                            dead_at = strftime('%Y-%m-%dT%H:%M:%f','now')
                        WHERE id = ?
                        """,
                        (item["work_item_id"],),
                    )

    def _dispatch_judge(self, item: dict):
        judge.run_judge(self, item)

    def _dispatch_architect(self, item: dict):
        architect.run_architect(self, item)

    def _dispatch_planner(self, item: dict):
        planner.run_planner(self, item)

    def process_forge_queue(self) -> None:
        """
        Прогон очереди forge: ``run_forge_queued_runs`` (тот же путь, что после ``forge_started``).

        При ``FACTORY_ORCHESTRATOR_ASYNC=1`` выполняется в отдельном потоке (tick не ждёт Qwen).
        """
        if not _env_orchestrator_async():
            forge.run_forge_queued_runs(self)
            return

        pending = self.conn.execute(
            """
            SELECT COUNT(*) AS c FROM runs r
            JOIN work_items wi ON wi.id = r.work_item_id
            WHERE r.role = ? AND r.run_type = ? AND r.status = 'queued'
              AND wi.status = 'in_progress'
            """,
            (Role.FORGE.value, RunType.IMPLEMENT.value),
        ).fetchone()["c"]
        if int(pending or 0) == 0:
            return

        active = self.conn.execute(
            """
            SELECT COUNT(*) AS c FROM runs
            WHERE role = ? AND run_type = ? AND status = 'running'
            """,
            (Role.FORGE.value, RunType.IMPLEMENT.value),
        ).fetchone()["c"]
        if int(active or 0) >= MAX_CONCURRENT_FORGE_RUNS:
            return

        global _active_forge_worker

        with _forge_worker_lock:
            if _active_forge_worker is not None and _active_forge_worker.is_alive():
                return

            def _work() -> None:
                from .composition import wire

                global _active_forge_worker
                try:
                    factory = wire(resolve_db_path())
                    conn = factory["conn"]
                    orch = factory["orchestrator"]
                    try:
                        forge.run_forge_queued_runs(orch)
                        conn.commit()
                    finally:
                        conn.close()
                except Exception as e:  # noqa: BLE001
                    try:
                        f2 = wire(resolve_db_path())
                        f2["logger"].log(
                            EventType.TASK_STATUS_CHANGED,
                            "system",
                            "orchestrator",
                            f"forge background worker: {e}",
                            severity=Severity.ERROR,
                            payload={"sub": "orchestrator_forge_async_error", "error": str(e)},
                        )
                        f2["conn"].commit()
                        f2["conn"].close()
                    except Exception:
                        pass
                finally:
                    with _forge_worker_lock:
                        _active_forge_worker = None

            self.logger.log(
                EventType.ORCHESTRATOR_AUTO_FORGE_STARTED,
                "system",
                "orchestrator",
                "orchestrator.auto_forge_started (background run_forge_queued_runs)",
                actor_role=Role.ORCHESTRATOR.value,
                payload={"sub": "orchestrator.auto_forge_started"},
                tags=["orchestrator", "forge", "async"],
            )
            _active_forge_worker = threading.Thread(
                target=_work,
                daemon=True,
                name="factory-forge-queued-runs",
            )
            _active_forge_worker.start()

    def process_review_queue(self) -> None:
        """
        Очередь ``review_inbox`` → ``reviewer.run_review`` (как ``_process_queue(REVIEW_INBOX)``).

        При ``FACTORY_ORCHESTRATOR_ASYNC=1`` — отдельный поток (tick не блокируется на ревью).
        """
        if not _env_orchestrator_async():
            self._process_queue(QueueName.REVIEW_INBOX, self._dispatch_reviewer)
            return

        active_rev = self.conn.execute(
            """
            SELECT COUNT(*) AS c FROM runs
            WHERE role = ? AND status = 'running'
            """,
            (Role.REVIEWER.value,),
        ).fetchone()["c"]
        if int(active_rev or 0) >= MAX_CONCURRENT_REVIEW_RUNS:
            return

        items = self.conn.execute(
            """
            SELECT wiq.work_item_id FROM work_item_queue wiq
            JOIN work_items wi ON wiq.work_item_id = wi.id
            WHERE wiq.queue_name = ?
              AND wiq.lease_owner IS NULL
              AND wiq.available_at <= strftime('%Y-%m-%dT%H:%M:%f','now')
              AND wiq.attempts < wiq.max_attempts
            ORDER BY wiq.priority ASC
            LIMIT 5
            """,
            (QueueName.REVIEW_INBOX.value,),
        ).fetchall()
        if not items:
            return

        global _active_review_worker
        with _review_worker_lock:
            if _active_review_worker is not None and _active_review_worker.is_alive():
                return

            def _work() -> None:
                from .composition import wire

                global _active_review_worker
                try:
                    factory = wire(resolve_db_path())
                    conn = factory["conn"]
                    orch = factory["orchestrator"]
                    try:
                        orch._process_queue(QueueName.REVIEW_INBOX, orch._dispatch_reviewer)
                        conn.commit()
                    finally:
                        conn.close()
                except Exception as e:  # noqa: BLE001
                    try:
                        from .composition import wire as _wire

                        f2 = _wire(resolve_db_path())
                        f2["logger"].log(
                            EventType.TASK_STATUS_CHANGED,
                            "system",
                            "orchestrator",
                            f"review background worker: {e}",
                            severity=Severity.ERROR,
                            payload={"sub": "orchestrator_review_async_error", "error": str(e)},
                        )
                        f2["conn"].commit()
                        f2["conn"].close()
                    except Exception:
                        pass
                finally:
                    with _review_worker_lock:
                        _active_review_worker = None

            self.logger.log(
                EventType.ORCHESTRATOR_AUTO_REVIEW_STARTED,
                "system",
                "orchestrator",
                "orchestrator.auto_review_started (background review_inbox)",
                actor_role=Role.ORCHESTRATOR.value,
                payload={"sub": "orchestrator.auto_review_started"},
                tags=["orchestrator", "review", "async"],
            )
            _active_review_worker = threading.Thread(
                target=_work,
                daemon=True,
                name="factory-review-inbox",
            )
            _active_review_worker.start()

    def _auto_enqueue_ready_atoms(self) -> None:
        """Атомы ``ready_for_work`` без строки в ``work_item_queue`` → ``forge_inbox`` (автономный режим)."""
        self.conn.execute(
            """
            INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at, attempts)
            SELECT wi.id, ?, 10, strftime('%Y-%m-%dT%H:%M:%f','now'), 0
            FROM work_items wi
            WHERE wi.kind IN ('atom', 'atm_change')
              AND wi.status = 'ready_for_work'
              AND wi.needs_human_review = 0
              AND NOT EXISTS (
                  SELECT 1 FROM work_item_queue wq WHERE wq.work_item_id = wi.id
              )
            """,
            (QueueName.FORGE_INBOX.value,),
        )

    def _dispatch_ready_atoms(self):
        active = self.conn.execute(
            """
            SELECT COUNT(*) AS c FROM runs
            WHERE role = ? AND run_type = ? AND status = 'running'
            """,
            (Role.FORGE.value, RunType.IMPLEMENT.value),
        ).fetchone()["c"]

        if active >= MAX_CONCURRENT_FORGE_RUNS:
            return

        items = self.conn.execute(
            """
            SELECT wiq.*, wi.kind, wi.status, wi.title
            FROM work_item_queue wiq
            JOIN work_items wi ON wiq.work_item_id = wi.id
            WHERE wiq.queue_name = ?
              AND wiq.lease_owner IS NULL
              AND wiq.available_at <= strftime('%Y-%m-%dT%H:%M:%f','now')
              AND wiq.attempts < wiq.max_attempts
              AND wi.status = 'ready_for_work'
            ORDER BY wiq.priority ASC
            LIMIT 5
            """,
            (QueueName.FORGE_INBOX.value,),
        ).fetchall()

        for item in items:
            try:
                self.sm.apply_transition(
                    item["work_item_id"],
                    "forge_started",
                    actor_role=Role.ORCHESTRATOR.value,
                )
            except AccountExhaustedError:
                raise
            except Exception as e:
                self.logger.log(
                    EventType.FORGE_FAILED,
                    "work_item",
                    item["work_item_id"],
                    str(e),
                    severity=Severity.ERROR,
                    work_item_id=item["work_item_id"],
                    payload={"sub": "forge_dispatch_error"},
                )

    def _dispatch_completion(self, item: dict):
        wi_id = item["work_item_id"]
        ok, _msg = self.sm.apply_transition(
            wi_id,
            "parent_complete",
            actor_role=Role.ORCHESTRATOR.value,
        )
        if ok:
            self.conn.execute(
                "DELETE FROM work_item_queue WHERE work_item_id = ?",
                (wi_id,),
            )

    def _dispatch_reviewer(self, item: dict):
        reviewer.run_review(self, item)

    def _escalate_review_rejected_to_judge(self):
        """
        review_rejected → sent_to_judge → ready_for_judge (st_16).

        Вызывается после обработки REVIEW_INBOX в том же тике, чтобы очередь
        сменилась на judge_inbox и задача не осталась в review_inbox со статусом
        уже не in_review.
        """
        rows = self.conn.execute(
            "SELECT id FROM work_items WHERE status = 'review_rejected'"
        ).fetchall()
        for r in rows:
            wi_id = r["id"]
            ok, msg = self.sm.apply_transition(
                wi_id,
                "sent_to_judge",
                actor_role=Role.ORCHESTRATOR.value,
            )
            if not ok:
                self.logger.log(
                    EventType.TASK_STATUS_CHANGED,
                    "work_item",
                    wi_id,
                    f"sent_to_judge не применён: {msg}",
                    severity=Severity.WARN,
                    work_item_id=wi_id,
                    payload={"msg": msg, "sub": "orchestrator_escalation_failed"},
                    tags=["fsm", "review_rejected"],
                )

    def _maybe_architect_proactive_scan(self):
        if ORCHESTRATOR_ARCHITECT_SCAN_TICKS <= 0:
            return
        if self._tick_counter % ORCHESTRATOR_ARCHITECT_SCAN_TICKS != 0:
            return
        architect.run_proactive_scan(self)

    def _expire_leases(self):
        expired = self.conn.execute(
            """
            SELECT work_item_id FROM work_item_queue
            WHERE lease_until IS NOT NULL
              AND lease_until < strftime('%Y-%m-%dT%H:%M:%f','now')
            """
        ).fetchall()

        if not expired:
            return

        ids = [row["work_item_id"] for row in expired]
        placeholders = ",".join("?" * len(ids))
        self.conn.execute(
            f"""
            UPDATE work_item_queue SET lease_owner = NULL, lease_until = NULL
            WHERE work_item_id IN ({placeholders})
            """,
            ids,
        )

        for item in expired:
            self.logger.log(
                EventType.TASK_STATUS_CHANGED,
                "work_item",
                item["work_item_id"],
                "Lease expired",
                work_item_id=item["work_item_id"],
                severity=Severity.WARN,
                payload={"sub": "lease_expired"},
            )

    def _check_blocked_items(self):
        blocked = self.conn.execute(
            "SELECT id FROM work_items WHERE status = 'blocked'"
        ).fetchall()

        for item in blocked:
            ok, _ = self.sm.guards.guard_all_deps_met(item["id"])
            if ok:
                self.sm.apply_transition(
                    item["id"],
                    "dependency_resolved",
                    actor_role=Role.ORCHESTRATOR.value,
                )

    def _handle_exhaustion(self, error: AccountExhaustedError):
        self._set_factory_status("paused_rate_limit")
        self.logger.log(
            EventType.TASK_STATUS_CHANGED,
            "system",
            "orchestrator",
            str(error),
            severity=Severity.WARN,
            tags=["rate_limit", "pause"],
            payload={"sub": "factory_paused"},
        )
        self._wait_for_reset()
        self._set_factory_status("running")

    def _wait_for_reset(self):
        now = datetime.now(timezone.utc)
        tomorrow = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=5, microsecond=0
        )
        wait_seconds = (tomorrow - now).total_seconds()

        self.logger.log(
            EventType.TASK_STATUS_CHANGED,
            "system",
            "orchestrator",
            f"Ожидание сброса лимитов: {wait_seconds:.0f} секунд (до {tomorrow.isoformat()})",
            severity=Severity.INFO,
            payload={
                "wait_seconds": wait_seconds,
                "reset_at": tomorrow.isoformat(),
                "sub": "waiting_for_reset",
            },
        )

        elapsed = 0
        while elapsed < wait_seconds and self._running:
            time.sleep(min(60, wait_seconds - elapsed))
            elapsed += 60

    def _set_factory_status(self, status: str):
        self.conn.execute(
            "UPDATE system_state SET value = ? WHERE key = 'factory_status'",
            (status,),
        )
        self.conn.commit()
