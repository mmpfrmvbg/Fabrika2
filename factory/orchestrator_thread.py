from __future__ import annotations

import logging
import sqlite3
import threading
import time
import traceback
from datetime import datetime, timezone

from .composition import wire
from .config import ORCHESTRATOR_TICK_INTERVAL_SECONDS
from .db import _db_path
from .models import EventType

_LOG = logging.getLogger("factory.api_server")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tick_interval_seconds() -> float:
    return ORCHESTRATOR_TICK_INTERVAL_SECONDS


class _OrchestratorThread:
    """
    Фоновый цикл оркестратора для api_server.

    Важно: создаёт СВОЁ SQLite-соединение (wire/init_db) и не переиспользует FastAPI-коннекты.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self.running = False
        self.last_tick: str | None = None
        self.ticks_total = 0
        self.items_processed_total = 0
        self.last_tick_processed: dict[str, int] = {}

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                self.running = True
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                daemon=True,
                name="factory-orchestrator-tick",
            )
            self.running = True
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self.running = False
            self._stop.set()
            t = self._thread
        if t and t.is_alive():
            t.join(timeout=5.0)

    def _run_loop(self) -> None:
        interval = _tick_interval_seconds()
        factory = None
        conn: sqlite3.Connection | None = None
        try:
            # отдельный граф/соединение для потока (retry на startup при lock contention)
            max_retries = 5
            last_err: Exception | None = None
            for attempt in range(max_retries):
                try:
                    factory = wire(_db_path())
                    conn = factory["conn"]
                    break
                except sqlite3.OperationalError as e:
                    last_err = e
                    if "locked" not in str(e).lower():
                        raise
                    if attempt >= max_retries - 1:
                        break
                    wait = min(2 ** (attempt + 1), 16)
                    _LOG.warning(
                        "[orchestrator] tick thread connect retry %s/%s, wait %ss: %s",
                        attempt + 1,
                        max_retries,
                        wait,
                        e,
                    )
                    time.sleep(wait)
            if conn is None:
                raise RuntimeError(
                    f"Failed to start orchestrator thread (db locked): {last_err}"
                )
            _LOG.info("[orchestrator] tick thread started interval=%ss db=%s", interval, _db_path())
            while not self._stop.is_set():
                try:
                    processed = self.tick_once(_factory=factory)
                    if processed:
                        self.items_processed_total += sum(processed.values())
                        parts = ", ".join(
                            f"{k}:{processed.get(k, 0)}" for k in ("forge", "review", "judge")
                        )
                        _LOG.info("[tick %s] %s", self.ticks_total, parts)
                except sqlite3.OperationalError as e:
                    msg = str(e).lower()
                    if "locked" not in msg:
                        raise
                    # lock contention: подождать и продолжить
                    time.sleep(5.0)
                    continue
                time.sleep(interval)
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc()
            _LOG.error("[orchestrator] tick thread crashed: %s\n%s", e, tb)
            with self._lock:
                self.running = False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception as e:
                    _LOG.debug("Failed to close orchestrator thread db connection: %s", e, exc_info=True)

    def tick_once(self, *, _factory: dict | None = None) -> dict[str, int]:
        """
        Выполняет один tick и возвращает сколько задач dequeued по очередям.
        """
        with self._lock:
            self.ticks_total += 1
        factory = _factory or wire(_db_path())
        conn: sqlite3.Connection = factory["conn"]
        orch = factory["orchestrator"]

        last_id = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS m FROM event_log"
        ).fetchone()["m"]
        orch.tick()

        rows = conn.execute(
            """
            SELECT event_type FROM event_log
            WHERE id > ?
              AND event_type IN (?, ?, ?)
            """,
            (
                last_id,
                EventType.FORGE_STARTED.value,
                EventType.REVIEW_STARTED.value,
                EventType.JUDGE_STARTED.value,
            ),
        ).fetchall()

        forge_n = 0
        review_n = 0
        judge_n = 0
        for r in rows:
            et = r["event_type"]
            if et == EventType.FORGE_STARTED.value:
                forge_n += 1
            elif et == EventType.REVIEW_STARTED.value:
                review_n += 1
            elif et == EventType.JUDGE_STARTED.value:
                judge_n += 1

        mapped = {
            "forge": forge_n,
            "review": review_n,
            "judge": judge_n,
        }
        self.last_tick = _utc_now_iso()
        self.last_tick_processed = mapped

        if _factory is None:
            try:
                conn.close()
            except Exception as e:
                _LOG.debug("Failed to close temporary tick connection: %s", e, exc_info=True)
        return {k: v for k, v in mapped.items() if v}
