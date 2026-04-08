"""
Read-only HTTP API для дашборда (SQLite WAL, mode=ro).

Запуск:
  python -m factory.api_server
  python -m factory --dashboard
  FACTORY_DB=... FACTORY_API_PORT=8000 python -m factory.api_server

БД: ``FACTORY_DB`` / ``FACTORY_DB_PATH`` или ``proekt/factory.db`` (см. ``resolve_db_path``).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from contextlib import asynccontextmanager
import io
import json
import logging
import os
import sqlite3
import sys
import threading
import time
import traceback
from uuid import uuid4
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Union

from fastapi import Body, Depends, FastAPI, HTTPException, Path as FastPath, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from .config import (
    API_HOST,
    API_PORT,
    ORCHESTRATOR_TICK_INTERVAL_SECONDS,
    AccountManager,
    load_dotenv,
    get_factory_api_key,
    resolve_db_path,
)
from .composition import wire
from .dashboard_api import _agents, _fsm_stub
from .dashboard_live_read import api_forge_inbox_simple
from .dashboard_api_read import get_work_items_paginated
from .dashboard_unified_journal import JournalFilters, api_journal_query
from .analytics_api import compute_analytics
from .workers_status import workers_status_payload
from .work_items_tree import build_work_items_tree, subtree_for_root_id
from .db import ensure_schema, gen_id, get_connection, resolve_effective_run_id
from .logging import FactoryLogger
from .models import EventType, Role
from .work_items import WorkItemOps
from .work_item_api_ops import (
    archive_work_item_subtree,
    cancel_work_item_subtree,
    delete_work_item_subtree,
    list_done_vision_roots_ready_to_archive,
)
from .agents.planner import decompose_with_planner
from .contracts.planner import PlannerInput
from .qwen_cli_runner import run_qwen_cli
from .chat_service import ChatService
from .logging_config import configure_logging

load_dotenv()

# Глобальный logger для endpoint (создаётся при первом использовании)
_logger: FactoryLogger | None = None
_LOG = logging.getLogger("factory.api_server")
_API_STARTED_AT_MONOTONIC = time.monotonic()
_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMITS_PER_MINUTE = {"GET": 300, "POST": 60}
_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_STATE: dict[tuple[str, str], dict[str, float | int]] = defaultdict(dict)
_RATE_LIMIT_TTL_SECONDS = 600


class WorkItemPatchRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10000)


class BulkArchiveRequest(BaseModel):
    ids: list[str] | None = None
    filter: str | None = None


class ImprovementReviewRequest(BaseModel):
    reviewed_by: str | None = Field(default="dashboard", min_length=1, max_length=128)


class VisionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10000)


class WorkItemCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    kind: str = Field(default="vision", min_length=1, max_length=32)
    parent_id: str | None = Field(default=None, min_length=1, max_length=128)
    priority: int = Field(default=0, ge=-100000, le=100000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=256)
    deadline_at: datetime | None = Field(default=None)


class ChatCreateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=20000)
    context: dict[str, Any] = Field(default_factory=dict)
    work_item_id: str | None = Field(default=None, min_length=1, max_length=128)


class QwenFixRequest(BaseModel):
    type: str | None = Field(default="unknown", max_length=128)
    message: str = Field(..., min_length=1, max_length=10000)
    context: dict[str, Any] = Field(default_factory=dict)


class RunCreateRequest(BaseModel):
    work_item_id: str = Field(..., min_length=1, max_length=128)
    correlation_id: str | None = Field(default=None, min_length=1, max_length=64)


def _valid_id(value: str, field: str) -> str:
    v = value.strip()
    if not v:
        raise HTTPException(status_code=400, detail=f"{field} must be a non-empty string")
    return v


def _get_logger(conn: sqlite3.Connection | None = None) -> FactoryLogger:
    """Получить logger для endpoint."""
    global _logger
    if _logger is None:
        try:
            tmp_conn = get_connection(_db_path())
            _logger = FactoryLogger(tmp_conn)
        except Exception as e:
            # Fallback: logger без connection
            _LOG.debug("Falling back to FactoryLogger(None): %s", e, exc_info=True)
            _logger = FactoryLogger(None)
    return _logger


async def require_api_key(request: Request) -> None:
    """Требует валидный ``X-API-Key`` для защищённых endpoint."""
    expected = get_factory_api_key()
    if not expected:
        raise RuntimeError(
            "FACTORY_API_KEY is not configured. Set FACTORY_API_KEY before starting the API server."
        )
    got = (request.headers.get("X-API-Key") or "").strip()
    if got != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

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


_orch_thread = _OrchestratorThread()


def _ensure_api_key_configured() -> str:
    api_key = get_factory_api_key()
    if not api_key:
        raise RuntimeError(
            "FACTORY_API_KEY is required for API server startup. "
            "Set FACTORY_API_KEY environment variable."
        )
    return api_key


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _ensure_api_key_configured()
    _orch_thread.start()
    try:
        yield
    finally:
        _orch_thread.stop()


app = FastAPI(title="Factory read-only API", version="1.0", lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    _LOG.exception("Unhandled API exception for %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "detail": "Internal server error",
            "path": request.url.path,
        },
    )


def health() -> dict[str, str]:
    return {"status": "ok"}


def api_health() -> dict[str, Any]:
    uptime_seconds = max(0.0, time.monotonic() - _API_STARTED_AT_MONOTONIC)
    db_connected = False
    worker_status: dict[str, Any] = {"active": 0, "workers": [], "leases_total": 0}
    orchestrator_heartbeat: dict[str, Any] = {
        "orchestrator_last_event_time": None,
        "orchestrator_seconds_since_last_event": None,
        "orchestrator_heartbeat_state": "none",
    }
    try:
        conn = _open_ro()
        try:
            conn.execute("SELECT 1").fetchone()
            db_connected = True
            worker_status = workers_status_payload(conn)
            orchestrator_heartbeat = _orchestrator_heartbeat_from_conn(conn)
        finally:
            conn.close()
    except HTTPException:
        db_connected = False
    except (sqlite3.OperationalError, sqlite3.DatabaseError) as exc:
        db_connected = False
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc
    return {
        "status": "ok",
        "db_connected": db_connected,
        "uptime_seconds": uptime_seconds,
        "worker_status": worker_status,
        "orchestrator_heartbeat": orchestrator_heartbeat,
        "version": {"api": app.version},
    }

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def api_key_auth_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    if request.url.path.startswith("/api") and request.method != "OPTIONS":
        try:
            await require_api_key(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


@app.middleware("http")
async def request_timing_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - started) * 1000.0
    _LOG.info(
        "request completed",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round(duration_ms, 2),
        },
    )
    return response


def _client_ip(request: Request) -> str:
    if request.client and request.client.host:
        client_host = request.client.host
        trusted_proxy = (os.environ.get("FACTORY_TRUSTED_PROXY") or "").strip()
        if trusted_proxy and client_host == trusted_proxy:
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                return forwarded.split(",")[0].strip() or client_host
        return client_host
    return "unknown"


def _rate_limit_meta(method: str, ip: str) -> dict[str, int]:
    limit = _RATE_LIMITS_PER_MINUTE.get(method.upper())
    if not limit:
        return {"limit": 0, "remaining": 0, "retry_after": 0, "is_limited": 0}

    key = (method.upper(), ip)
    now = time.time()
    with _RATE_LIMIT_LOCK:
        expired_keys = [
            state_key
            for state_key, state in _RATE_LIMIT_STATE.items()
            if now - float(state.get("last_access", 0.0)) > _RATE_LIMIT_TTL_SECONDS
        ]
        for state_key in expired_keys:
            _RATE_LIMIT_STATE.pop(state_key, None)

        state = _RATE_LIMIT_STATE.get(key) or {"window_start": now, "count": 0}
        window_start = float(state.get("window_start", now))
        count = int(state.get("count", 0))
        elapsed = now - window_start
        if elapsed >= _RATE_LIMIT_WINDOW_SECONDS:
            window_start = now
            count = 0
            elapsed = 0
        count += 1
        _RATE_LIMIT_STATE[key] = {
            "window_start": window_start,
            "count": count,
            "last_access": now,
        }
        remaining = max(0, limit - count)
        retry_after = max(0, int(_RATE_LIMIT_WINDOW_SECONDS - elapsed))
        is_limited = 1 if count > limit else 0
    return {
        "limit": limit,
        "remaining": remaining,
        "retry_after": retry_after,
        "is_limited": is_limited,
    }


@app.middleware("http")
async def rate_limit_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    ip = _client_ip(request)
    meta = _rate_limit_meta(request.method, ip)
    response: Response
    if meta["is_limited"]:
        response = JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded"},
        )
        response.headers["Retry-After"] = str(meta["retry_after"])
    else:
        response = await call_next(request)

    if meta["limit"] > 0:
        response.headers["X-RateLimit-Limit"] = str(meta["limit"])
        response.headers["X-RateLimit-Remaining"] = str(meta["remaining"])
    else:
        response.headers["X-RateLimit-Limit"] = "unlimited"
        response.headers["X-RateLimit-Remaining"] = "unlimited"
    return response


def _db_path() -> Path:
    raw = os.environ.get("FACTORY_DB") or os.environ.get("FACTORY_DB_PATH")
    return resolve_db_path(Path(raw)) if raw else resolve_db_path()


def _open_ro() -> sqlite3.Connection:
    path = _db_path()
    if not path.exists():
        raise HTTPException(status_code=503, detail=f"Database not found: {path}")
    try:
        return get_connection(path, read_only=True)
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail=f"Database not found: {path}") from None


def _open_rw() -> sqlite3.Connection:
    """
    RW-соединение для минимальных write-операций дашборда (создание Vision).
    В отличие от `_open_ro` не включает `query_only`.
    """
    path = _db_path()
    ensure_schema(path)
    return get_connection(path)


def _row(d: sqlite3.Row) -> dict[str, Any]:
    return {k: d[k] for k in d.keys()}


def _rows(rs: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [_row(r) for r in rs]


def _serialize_run_row(d: sqlite3.Row) -> dict[str, Any]:
    out = _row(d)
    out["source_run_id"] = out.get("source_run_id")
    out["dry_run"] = bool(out.get("dry_run"))
    return out


def _serialize_runs(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [_serialize_run_row(r) for r in rows]


def _serialize_export_work_items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    work_item_rows = conn.execute(
        "SELECT * FROM work_items ORDER BY created_at ASC, id ASC"
    ).fetchall()
    run_rows = conn.execute(
        "SELECT * FROM runs ORDER BY started_at ASC, id ASC"
    ).fetchall()
    event_rows = conn.execute(
        "SELECT * FROM event_log ORDER BY event_time ASC, id ASC"
    ).fetchall()

    runs_by_work_item: dict[str, list[dict[str, Any]]] = {}
    for row in run_rows:
        run = _serialize_run_row(row)
        wi_id = str(run.get("work_item_id") or "")
        if wi_id:
            runs_by_work_item.setdefault(wi_id, []).append(run)

    events_by_work_item: dict[str, list[dict[str, Any]]] = {}
    for row in event_rows:
        event = _row(row)
        wi_id = str(event.get("work_item_id") or "")
        if wi_id:
            events_by_work_item.setdefault(wi_id, []).append(event)

    items: list[dict[str, Any]] = []
    for row in work_item_rows:
        wi = _row(row)
        wi_id = str(wi["id"])
        wi["runs"] = runs_by_work_item.get(wi_id, [])
        wi["events"] = events_by_work_item.get(wi_id, [])
        items.append(wi)
    return items


def _work_items_export_csv(items: list[dict[str, Any]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["work_item_id", "kind", "status", "title", "runs_json", "events_json"])
    for item in items:
        writer.writerow(
            [
                item.get("id", ""),
                item.get("kind", ""),
                item.get("status", ""),
                item.get("title", ""),
                json.dumps(item.get("runs", []), ensure_ascii=False),
                json.dumps(item.get("events", []), ensure_ascii=False),
            ]
        )
    return out.getvalue()


def _queue_depths_from_conn(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT queue_name, COUNT(*) AS c
        FROM work_item_queue
        WHERE queue_name IN ('forge_inbox','review_inbox','judge_inbox')
        GROUP BY queue_name
        """
    ).fetchall()
    out = {r["queue_name"]: int(r["c"]) for r in rows}
    for k in ("forge_inbox", "review_inbox", "judge_inbox"):
        out.setdefault(k, 0)
    return out


def _parse_event_time_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    s = str(ts).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _orchestrator_heartbeat_from_conn(conn: sqlite3.Connection) -> dict[str, Any]:
    """Последнее событие с actor_role orchestrator (для UI heartbeat)."""
    row = conn.execute(
        """
        SELECT MAX(event_time) AS t FROM event_log
        WHERE LOWER(COALESCE(actor_role, '')) = ?
        """,
        (Role.ORCHESTRATOR.value,),
    ).fetchone()
    ts = row["t"] if row else None
    dt = _parse_event_time_iso(ts) if ts else None
    if not dt:
        return {
            "orchestrator_last_event_time": None,
            "orchestrator_seconds_since_last_event": None,
            "orchestrator_heartbeat_state": "none",
        }
    now = datetime.now(timezone.utc)
    sec = max(0.0, (now - dt).total_seconds())
    if sec < 30.0:
        state = "active"
    elif sec < 60.0:
        state = "warn"
    else:
        state = "stale"
    return {
        "orchestrator_last_event_time": ts,
        "orchestrator_seconds_since_last_event": sec,
        "orchestrator_heartbeat_state": state,
    }


def api_metrics() -> dict[str, Any]:
    conn = _open_ro()
    try:
        work_items_total = int(conn.execute("SELECT COUNT(*) AS c FROM work_items").fetchone()["c"])
        work_items_by_status = {
            r["status"]: int(r["c"])
            for r in conn.execute(
                "SELECT status, COUNT(*) AS c FROM work_items GROUP BY status"
            ).fetchall()
        }
        runs_total = int(conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"])
        runs_last_24h = int(
            conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM runs
                WHERE started_at IS NOT NULL
                  AND julianday(started_at) >= julianday('now', '-1 day')
                """
            ).fetchone()["c"]
        )
        failed_runs_last_24h = int(
            conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM runs
                WHERE started_at IS NOT NULL
                  AND julianday(started_at) >= julianday('now', '-1 day')
                  AND LOWER(COALESCE(status, '')) IN ('failed', 'error')
                """
            ).fetchone()["c"]
        )
        avg_run_duration_seconds = float(
            conn.execute(
                """
                SELECT COALESCE(AVG((julianday(finished_at) - julianday(started_at)) * 86400.0), 0.0) AS s
                FROM runs
                WHERE started_at IS NOT NULL
                  AND finished_at IS NOT NULL
                  AND julianday(finished_at) >= julianday(started_at)
                """
            ).fetchone()["s"]
            or 0.0
        )
        orchestrator_running = bool(
            conn.execute(
                """
                SELECT EXISTS(
                    SELECT 1
                    FROM event_log
                    WHERE LOWER(COALESCE(actor_role, '')) = 'orchestrator'
                      AND julianday(event_time) >= julianday('now', ?)
                ) AS is_running
                """,
                (f"-{2 * _tick_interval_seconds()} seconds",),
            ).fetchone()["is_running"]
        )
        return {
            "work_items_total": work_items_total,
            "work_items_by_status": work_items_by_status,
            "runs_total": runs_total,
            "runs_last_24h": runs_last_24h,
            "failed_runs_last_24h": failed_runs_last_24h,
            "avg_run_duration_seconds": avg_run_duration_seconds,
            "orchestrator_running": orchestrator_running,
        }
    finally:
        conn.close()


def orchestrator_status() -> dict[str, Any]:
    conn = _open_ro()
    try:
        qd = _queue_depths_from_conn(conn)
    finally:
        conn.close()
    return {
        "running": bool(_orch_thread.running),
        "last_tick": _orch_thread.last_tick,
        "ticks_total": int(_orch_thread.ticks_total),
        "items_processed": int(_orch_thread.items_processed_total),
        "last_tick_processed": dict(_orch_thread.last_tick_processed or {}),
        "queue_depths": qd,
    }


def orchestrator_start(_: None = Depends(require_api_key)) -> dict[str, Any]:
    _orch_thread.start()
    return orchestrator_status()


def orchestrator_stop(_: None = Depends(require_api_key)) -> dict[str, Any]:
    _orch_thread.stop()
    return orchestrator_status()


def orchestrator_health() -> dict[str, Any]:
    """Heartbeat по event_log (actor_role=orchestrator), не путать с /api/orchestrator/status (поток tick)."""
    conn = _open_ro()
    try:
        h = _orchestrator_heartbeat_from_conn(conn)
        return {"ok": True, **h}
    finally:
        conn.close()


def orchestrator_tick(_: None = Depends(require_api_key)) -> dict[str, Any]:
    processed = _orch_thread.tick_once()
    conn = _open_ro()
    try:
        qd = _queue_depths_from_conn(conn)
    finally:
        conn.close()
    return {
        "ok": True,
        "processed": processed,
        "queue_depths": qd,
        "status": {
            "running": bool(_orch_thread.running),
            "last_tick": _orch_thread.last_tick,
            "ticks_total": int(_orch_thread.ticks_total),
            "items_processed": int(_orch_thread.items_processed_total),
        },
    }


def list_work_items(
    status: str | None = None,
    parent_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    conn = _open_ro()
    try:
        return get_work_items_paginated(
            conn,
            limit=limit,
            offset=offset,
            filters={"status": status, "parent_id": parent_id},
        )
    finally:
        conn.close()


def export_work_items(
    format: str = Query("json", pattern="^(json|csv)$"),  # noqa: A002
) -> Response:
    """Export all work items with nested runs/events as a downloadable file."""
    conn = _open_ro()
    try:
        items = _serialize_export_work_items(conn)
    finally:
        conn.close()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if format == "csv":
        return Response(
            content=_work_items_export_csv(items),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="work-items-export-{ts}.csv"'},
        )

    return Response(
        content=json.dumps({"items": items, "total": len(items)}, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="work-items-export-{ts}.json"'},
    )


def work_items_tree_endpoint() -> dict[str, Any]:
    """Полное дерево задач (корни без parent_id). Должен быть объявлен до ``/api/work-items/{wi_id}``."""
    conn = _open_ro()
    try:
        tree = build_work_items_tree(conn)
        return {"tree": tree}
    finally:
        conn.close()


_EDITABLE_STATUSES = frozenset(
    {"draft", "planned", "ready_for_judge", "judge_rejected"}
)


def post_work_item_cancel(
    wi_id: str = FastPath(..., min_length=1, max_length=128),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """FSM creator_cancelled + каскад по поддереву (post-order)."""
    factory = wire(_db_path())
    conn: sqlite3.Connection = factory["conn"]
    sm = factory["sm"]
    logger: FactoryLogger = factory["logger"]
    try:
        logger.log(
            EventType.API_WORK_ITEM_CANCEL,
            "work_item",
            wi_id,
            "POST /api/work-items/…/cancel",
            work_item_id=wi_id,
            actor_role=Role.CREATOR.value,
            tags=["api", "cancel"],
        )
        conn.commit()
        n, err = cancel_work_item_subtree(sm, conn, wi_id, actor_role=Role.CREATOR.value)
        if err:
            if err == "work_item not found":
                raise HTTPException(status_code=404, detail=err)
            raise HTTPException(status_code=400, detail=err)
        return {"ok": True, "cancelled_count": n}
    finally:
        conn.close()


def post_work_item_archive(
    wi_id: str = FastPath(..., min_length=1, max_length=128),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """FSM archive_sweep для done и всех done-потомков."""
    factory = wire(_db_path())
    conn = factory["conn"]
    sm = factory["sm"]
    logger: FactoryLogger = factory["logger"]
    try:
        logger.log(
            EventType.API_WORK_ITEM_ARCHIVE,
            "work_item",
            wi_id,
            "POST /api/work-items/…/archive",
            work_item_id=wi_id,
            actor_role=Role.CREATOR.value,
            tags=["api", "archive"],
        )
        conn.commit()
        n, err = archive_work_item_subtree(sm, conn, wi_id, actor_role=Role.CREATOR.value)
        if err:
            if err == "work_item not found":
                raise HTTPException(status_code=404, detail=err)
            raise HTTPException(status_code=400, detail=err)
        return {"ok": True, "archived_count": n}
    finally:
        conn.close()


def patch_work_item(
    wi_id: str = FastPath(..., min_length=1, max_length=128),
    body: WorkItemPatchRequest = Body(...),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    title = body.title
    description = body.description
    if title is None and description is None:
        raise HTTPException(
            status_code=400, detail="expected title and/or description"
        )
    conn = _open_rw()
    logger = FactoryLogger(conn)
    try:
        row = conn.execute(
            "SELECT * FROM work_items WHERE id = ?", (wi_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="work_item not found")
        st = row["status"]
        if st not in _EDITABLE_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"edit not allowed for status {st}",
            )
        if title is not None and description is not None:
            conn.execute(
                "UPDATE work_items SET title = ?, description = ? WHERE id = ?",
                (str(title).strip() or row["title"], str(description), wi_id),
            )
        elif title is not None:
            conn.execute(
                "UPDATE work_items SET title = ? WHERE id = ?",
                (str(title).strip() or row["title"], wi_id),
            )
        elif description is not None:
            conn.execute(
                "UPDATE work_items SET description = ? WHERE id = ?",
                (str(description), wi_id),
            )
        else:
            raise HTTPException(status_code=400, detail="nothing to update")
        logger.log(
            EventType.WORK_ITEM_UPDATED,
            "work_item",
            wi_id,
            "work_item.updated via PATCH",
            work_item_id=wi_id,
            actor_role=Role.CREATOR.value,
            payload={
                "title": title if title is not None else row["title"],
                "description": description
                if description is not None
                else row["description"],
            },
            tags=["api", "patch"],
        )
        conn.commit()
        upd = conn.execute("SELECT * FROM work_items WHERE id = ?", (wi_id,)).fetchone()
        return {"work_item": _row(upd)}
    finally:
        conn.close()


def delete_work_item_endpoint(
    wi_id: str = FastPath(..., min_length=1, max_length=128),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    conn = _open_rw()
    logger = FactoryLogger(conn)
    try:
        n, err = delete_work_item_subtree(conn, logger, wi_id)
        if err:
            if err == "work_item not found":
                raise HTTPException(status_code=404, detail=err)
            raise HTTPException(status_code=400, detail=err)
        conn.commit()
        return {"ok": True, "deleted_count": n}
    finally:
        conn.close()


def post_bulk_archive(
    body: BulkArchiveRequest = Body(default=BulkArchiveRequest()),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Архивирует несколько корней (обычно Vision в done)."""
    ids = body.ids
    filt = (body.filter or "").strip()
    factory = wire(_db_path())
    conn = factory["conn"]
    sm = factory["sm"]
    try:
        if filt == "all_done_visions":
            target_ids = list_done_vision_roots_ready_to_archive(conn)
        elif isinstance(ids, list) and ids:
            target_ids = [_valid_id(str(x), "ids[]") for x in ids]
        else:
            raise HTTPException(
                status_code=400,
                detail='body must contain ids: [...] or filter: "all_done_visions"',
            )
        total = 0
        errors: list[str] = []
        for vid in target_ids:
            n, err = archive_work_item_subtree(
                sm, conn, vid, actor_role=Role.CREATOR.value
            )
            if err:
                errors.append(f"{vid}: {err}")
            else:
                total += n
        return {
            "ok": not errors,
            "archived_count": total,
            "errors": errors,
            "processed_roots": len(target_ids),
        }
    finally:
        conn.close()


def post_work_item_run(
    wi_id: str = FastPath(..., min_length=1, max_length=128),
    _: None = Depends(require_api_key),
) -> Any:
    """Запуск forge для атома (тот же путь, что POST /api/tasks/…/forge-run в dashboard_api)."""
    correlation_id = str(uuid4())
    from .dashboard_task_run import accept_dashboard_task_run

    ok, body, status = accept_dashboard_task_run(wi_id, correlation_id=correlation_id)
    if not ok:
        return JSONResponse(status_code=status, content=body)
    return {
        "started": True,
        "run_id": body.get("run_id"),
        "correlation_id": correlation_id,
        "ok": body.get("ok", True),
        "status": body.get("status", "started"),
        "message": body.get("message", "accepted"),
    }


def post_tasks_forge_run_compat(
    wi_id: str = FastPath(..., min_length=1, max_length=128),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Совместимость с factory-os.html (старый путь)."""
    return post_work_item_run(wi_id)


def get_work_item(wi_id: str = FastPath(..., min_length=1, max_length=128)) -> dict[str, Any]:
    conn = _open_ro()
    try:
        wi = conn.execute("SELECT * FROM work_items WHERE id = ?", (wi_id,)).fetchone()
        if not wi:
            raise HTTPException(status_code=404, detail="work_item not found")
        files = conn.execute(
            """
            SELECT path, intent, description, required
            FROM work_item_files
            WHERE work_item_id = ?
            ORDER BY path
            """,
            (wi_id,),
        ).fetchall()
        # Atom diagnostics counters (no schema columns; derived from existing tables).
        forge_attempts = conn.execute(
            "SELECT COUNT(*) AS c FROM runs WHERE work_item_id = ? AND role = 'forge'",
            (wi_id,),
        ).fetchone()["c"]
        review_rejections = conn.execute(
            "SELECT COUNT(*) AS c FROM review_results WHERE work_item_id = ? AND verdict != 'approved'",
            (wi_id,),
        ).fetchone()["c"]
        judge_rejections = conn.execute(
            "SELECT COUNT(*) AS c FROM judge_verdicts WHERE work_item_id = ? AND verdict != 'approved'",
            (wi_id,),
        ).fetchone()["c"]
        ch = conn.execute(
            "SELECT * FROM work_items WHERE parent_id = ? ORDER BY created_at",
            (wi_id,),
        ).fetchall()
        qlease = conn.execute(
            """
            SELECT queue_name, lease_owner, lease_until, attempts
            FROM work_item_queue WHERE work_item_id = ?
            """,
            (wi_id,),
        ).fetchone()
        wi_out = {
            **_row(wi),
            "files": _rows(files),
            "forge_attempts": int(forge_attempts),
            "review_rejections": int(review_rejections),
            "judge_rejections": int(judge_rejections),
        }
        if qlease:
            wi_out["queue_lease"] = _row(qlease)
        return {
            "work_item": wi_out,
            "children": _rows(ch),
        }
    finally:
        conn.close()


def get_task_bundle(wi_id: str = FastPath(..., min_length=1, max_length=128)) -> dict[str, Any]:
    """Совместимость с factory-os.html (openDetail)."""
    conn = _open_ro()
    try:
        wi = conn.execute("SELECT * FROM work_items WHERE id = ?", (wi_id,)).fetchone()
        if not wi:
            raise HTTPException(status_code=404, detail="work_item not found")
        runs = conn.execute(
            """
            SELECT id, role, run_type, status, started_at, finished_at, work_item_id, correlation_id,
                   error_summary, tokens_used, source_run_id, dry_run
            FROM runs WHERE work_item_id = ?
            ORDER BY started_at DESC
            """,
            (wi_id,),
        ).fetchall()
        files = conn.execute(
            """
            SELECT path, intent, description, required
            FROM work_item_files WHERE work_item_id = ?
            ORDER BY path
            """,
            (wi_id,),
        ).fetchall()
        ev = conn.execute(
            """
            SELECT id, event_time, event_type, actor_role, severity, message
            FROM event_log
            WHERE work_item_id = ?
            ORDER BY event_time DESC, id DESC
            LIMIT 10
            """,
            (wi_id,),
        ).fetchall()
        return {
            "work_item": {**_row(wi), "files": _rows(files), "event_log": _rows(ev)},
            "runs": _serialize_runs(runs),
            "comments": [],
        }
    finally:
        conn.close()


def work_items_legacy(
    id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    conn = _open_ro()
    try:
        if not id or not id.strip():
            return get_work_items_paginated(
                conn,
                limit=limit,
                offset=offset,
                filters={"status": status},
            )
        wi = conn.execute("SELECT * FROM work_items WHERE id = ?", (id,)).fetchone()
        if not wi:
            raise HTTPException(status_code=404, detail="not found")
        return {"work_item": _row(wi)}
    finally:
        conn.close()


def create_work_item_legacy(
    body: WorkItemCreateRequest | dict[str, Any] = Body(...),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    payload = body if isinstance(body, WorkItemCreateRequest) else WorkItemCreateRequest.model_validate(body)
    conn = _open_rw()
    try:
        idempotency_key = payload.idempotency_key.strip() if payload.idempotency_key else None
        if idempotency_key:
            existing = conn.execute(
                "SELECT id FROM work_items WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                return JSONResponse(  # type: ignore[return-value]
                    status_code=409,
                    content={"error": "duplicate", "existing_id": existing["id"]},
                )

        wi_id = gen_id("wi")
        parent_id = payload.parent_id.strip() if payload.parent_id else None
        root_id = wi_id
        depth = 0
        if parent_id:
            parent = conn.execute(
                "SELECT root_id, planning_depth FROM work_items WHERE id = ?",
                (parent_id,),
            ).fetchone()
            if not parent:
                raise HTTPException(status_code=404, detail="parent not found")
            root_id = str(parent["root_id"])
            depth = int(parent["planning_depth"] or 0) + 1

        correlation_id = str(uuid4())
        conn.execute(
            """
            INSERT INTO work_items (
                id, parent_id, root_id, kind, title, description, status,
                creator_role, owner_role, planning_depth, priority, correlation_id,
                idempotency_key, deadline_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'draft', 'creator', 'creator', ?, ?, ?, ?, ?)
            """,
            (
                wi_id,
                parent_id,
                root_id,
                payload.kind.strip().lower(),
                payload.title.strip(),
                payload.description.strip() if payload.description else None,
                depth,
                int(payload.priority),
                correlation_id,
                idempotency_key,
                payload.deadline_at.astimezone(timezone.utc).isoformat() if payload.deadline_at else None,
            ),
        )
        conn.commit()
        wi = conn.execute("SELECT * FROM work_items WHERE id = ?", (wi_id,)).fetchone()
        return {"ok": True, "work_item": _row(wi)}
    except sqlite3.IntegrityError:
        if idempotency_key:
            existing = conn.execute(
                "SELECT id FROM work_items WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                return JSONResponse(  # type: ignore[return-value]
                    status_code=409,
                    content={"error": "duplicate", "existing_id": existing["id"]},
                )
        raise
    finally:
        conn.close()


def create_run(
    body: RunCreateRequest = Body(...),
    _: None = Depends(require_api_key),
) -> Any:
    wi_id = body.work_item_id.strip()
    correlation_id = (body.correlation_id or "").strip() or str(uuid4())
    conn = _open_rw()
    try:
        wi = conn.execute("SELECT id FROM work_items WHERE id = ?", (wi_id,)).fetchone()
        if not wi:
            raise HTTPException(status_code=404, detail="work_item not found")
        run_id = gen_id("run")
        conn.execute("UPDATE work_items SET correlation_id = ? WHERE id = ?", (correlation_id, wi_id))
        conn.execute(
            """
            INSERT INTO runs (
                id, work_item_id, agent_id, role, run_type, status, correlation_id
            )
            VALUES (?, ?, 'agent_forge', 'forge', 'implement', 'queued', ?)
            """,
            (run_id, wi_id, correlation_id),
        )
        logger = FactoryLogger(conn)
        logger.log(
            EventType.RUN_STARTED,
            "run",
            run_id,
            "Run accepted via POST /api/runs",
            run_id=run_id,
            work_item_id=wi_id,
            actor_role=Role.CREATOR.value,
            payload={"source": "api.runs.create", "correlation_id": correlation_id},
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "ok": True,
        "work_item_id": wi_id,
        "run_id": run_id,
        "correlation_id": correlation_id,
        "status": "accepted",
    }


def runs_for_work_item(wi_id: str = FastPath(..., min_length=1, max_length=128)) -> dict[str, Any]:
    conn = _open_ro()
    try:
        rows = conn.execute(
            """
            SELECT id, role, run_type, status, started_at, finished_at, correlation_id
                   , source_run_id, dry_run
            FROM runs WHERE work_item_id = ?
            ORDER BY started_at DESC
            """,
            (wi_id,),
        ).fetchall()
        return {"items": _serialize_runs(rows)}
    finally:
        conn.close()


def list_runs(
    work_item_id: str | None = None,
    limit: int = Query(120, ge=1, le=500),
) -> dict[str, Any]:
    conn = _open_ro()
    try:
        if work_item_id:
            rows = conn.execute(
                """
                SELECT id, role, run_type, status, started_at, finished_at, work_item_id, correlation_id
                       , source_run_id, dry_run
                FROM runs WHERE work_item_id = ?
                ORDER BY started_at DESC LIMIT ?
                """,
                (work_item_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, role, run_type, status, started_at, finished_at, work_item_id, correlation_id
                       , source_run_id, dry_run
                FROM runs ORDER BY started_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return {"items": _serialize_runs(rows)}
    finally:
        conn.close()


def get_run_detail(run_id: str = FastPath(..., min_length=1, max_length=128)) -> dict[str, Any]:
    conn = _open_ro()
    try:
        r = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if r:
            effective_run_id = resolve_effective_run_id(conn, run_id) or run_id
            steps = conn.execute(
                "SELECT * FROM run_steps WHERE run_id = ? ORDER BY step_no",
                (effective_run_id,),
            ).fetchall()
            fcs = conn.execute(
                "SELECT * FROM file_changes WHERE run_id = ? ORDER BY created_at",
                (effective_run_id,),
            ).fetchall()
            return {
                "run": {**_serialize_run_row(r), "effective_run_id": effective_run_id},
                "run_steps": _rows(steps),
                "file_changes": _rows(fcs),
            }
        rows = conn.execute(
            """
            SELECT * FROM runs WHERE work_item_id = ?
            ORDER BY started_at DESC
            """,
            (run_id,),
        ).fetchall()
        if rows:
            return {"runs": _serialize_runs(rows), "work_item_id": run_id}
        raise HTTPException(status_code=404, detail="run / work_item not found")
    finally:
        conn.close()


def get_run_steps(run_id: str = FastPath(..., min_length=1, max_length=128)) -> dict[str, Any]:
    conn = _open_ro()
    try:
        effective_run_id = resolve_effective_run_id(conn, run_id) or run_id
        steps = conn.execute(
            "SELECT * FROM run_steps WHERE run_id = ? ORDER BY step_no",
            (effective_run_id,),
        ).fetchall()
        if not steps:
            raise HTTPException(status_code=404, detail="no steps for this run id")
        return {"items": _rows(steps), "effective_run_id": effective_run_id}
    finally:
        conn.close()


def get_effective_run_id(run_id: str = FastPath(..., min_length=1, max_length=128)) -> dict[str, Any]:
    conn = _open_ro()
    try:
        r = conn.execute("SELECT id FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="run not found")
        return {"effective_run_id": resolve_effective_run_id(conn, run_id) or run_id}
    finally:
        conn.close()


def list_events(
    limit: int = Query(10, ge=1, le=500),
    work_item_id: str | None = None,
    event_type: str | None = None,
    stream: bool = False,
) -> Union[dict[str, Any], StreamingResponse]:
    conn = _open_ro()
    try:
        q = "SELECT * FROM event_log WHERE 1=1"
        params: list[Any] = []
        if work_item_id:
            q += " AND work_item_id = ?"
            params.append(work_item_id)
        if event_type:
            q += " AND event_type LIKE ?"
            params.append(f"%{event_type}%")
        q += " ORDER BY event_time DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        items = _rows(rows)
        if stream:
            def _event_stream():
                for item in items:
                    yield f"event: {item.get('event_type', 'event')}\n"
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            return StreamingResponse(_event_stream(), media_type="text/event-stream")
        return {"items": items, "limit": limit}
    finally:
        conn.close()


@app.get("/api/events")
async def stream_events(
    request: Request,
    last_event_id: int = Query(default=0, ge=0),
    once: bool = Query(default=False),
) -> StreamingResponse:
    async def _event_stream() -> Any:
        cursor = int(last_event_id)
        while True:
            if await request.is_disconnected():
                break
            conn = _open_ro()
            try:
                rows = conn.execute(
                    """
                    SELECT id, event_type, payload
                    FROM event_log
                    WHERE id > ?
                    ORDER BY id ASC
                    """,
                    (cursor,),
                ).fetchall()
            finally:
                conn.close()

            if rows:
                for row in rows:
                    cursor = int(row["id"])
                    payload_raw = row["payload"]
                    payload_obj: dict[str, Any] = {}
                    if isinstance(payload_raw, str) and payload_raw.strip():
                        try:
                            parsed = json.loads(payload_raw)
                            if isinstance(parsed, dict):
                                payload_obj = parsed
                        except Exception:
                            payload_obj = {}
                    event_data = {
                        "id": cursor,
                        "type": row["event_type"],
                        "payload": payload_obj,
                    }
                    yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"
            else:
                yield ": keep-alive\n\n"
            if once:
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/journal")
def journal(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    work_item_id: str | None = None,
    run_id: str | None = None,
    root_id: str | None = None,
    kind: str | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    conn = _open_ro()
    try:
        flt = JournalFilters(
            work_item_id=work_item_id,
            run_id=run_id,
            root_id=root_id,
            kind=kind,
            role=role,
        )
        return api_journal_query(conn, flt, limit=limit, offset=offset)
    finally:
        conn.close()


def _load_judgements_items(
    conn: sqlite3.Connection, work_item_id: str | None, limit: int
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    qjv = """
        SELECT id, work_item_id, verdict, payload_json, failed_guards_json,
               rejection_reason_code, created_at, run_id
        FROM judge_verdicts
        WHERE 1=1
    """
    pjv: list[Any] = []
    if work_item_id:
        qjv += " AND work_item_id = ?"
        pjv.append(work_item_id)
    qjv += " ORDER BY created_at DESC LIMIT ?"
    pjv.append(limit)
    try:
        jv = conn.execute(qjv, pjv).fetchall()
    except sqlite3.OperationalError as e:
        _LOG.debug("judge_verdicts table unavailable while loading judgements: %s", e)
        jv = []
    for r in jv:
        issues: Any = []
        p: dict[str, Any] = {}
        try:
            p = json.loads(r["payload_json"] or "{}")
            if isinstance(p, dict):
                issues = p.get("failed_guards") or p.get("issues") or []
            else:
                issues = []
        except json.JSONDecodeError:
            issues = []
        try:
            if r["failed_guards_json"]:
                issues = json.loads(r["failed_guards_json"])
        except (json.JSONDecodeError, TypeError) as e:
            _LOG.debug("Failed to parse failed_guards_json for verdict %s: %s", r["id"], e)
        used_el = None
        if isinstance(p, dict):
            used_el = p.get("used_event_log")
        items.append(
            {
                "id": r["id"],
                "work_item_id": r["work_item_id"],
                "role": "judge",
                "verdict": r["verdict"],
                "reason_code": r["rejection_reason_code"] or "",
                "issues": issues if isinstance(issues, list) else [],
                "created_at": r["created_at"],
                "run_id": r["run_id"],
                "summary": (r["verdict"] or "")[:200],
                "used_event_log": used_el if isinstance(used_el, bool) else False,
            }
        )
    qrr = """
        SELECT id, work_item_id, verdict, issues_json, payload_json, created_at, reviewer_run_id
        FROM review_results
        WHERE 1=1
    """
    prr: list[Any] = []
    if work_item_id:
        qrr += " AND work_item_id = ?"
        prr.append(work_item_id)
    qrr += " ORDER BY created_at DESC LIMIT ?"
    prr.append(limit)
    try:
        rr = conn.execute(qrr, prr).fetchall()
    except sqlite3.OperationalError as e:
        _LOG.debug("review_results table unavailable while loading judgements: %s", e)
        rr = []
    for r in rr:
        issues = []
        try:
            issues = json.loads(r["issues_json"] or "[]")
        except json.JSONDecodeError:
            issues = []
        items.append(
            {
                "id": r["id"],
                "work_item_id": r["work_item_id"],
                "role": "reviewer",
                "verdict": r["verdict"],
                "reason_code": "",
                "issues": issues,
                "created_at": r["created_at"],
                "run_id": r["reviewer_run_id"],
                "summary": (r["verdict"] or "")[:200],
            }
        )
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return items[:limit]


@app.get("/api/judgements")
def judgements(
    work_item_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    conn = _open_ro()
    try:
        return {"items": _load_judgements_items(conn, work_item_id, limit)}
    finally:
        conn.close()


@app.get("/api/verdicts")
@app.get("/api/judge_verdicts")
def judge_verdicts(
    work_item_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Compatibility endpoint: always returns a JSON list for dashboard verdict pages."""
    conn = _open_ro()
    try:
        return _load_judgements_items(conn, work_item_id, limit)
    finally:
        conn.close()


@app.get("/api/tree")
def tree() -> dict[str, Any]:
    conn = _open_ro()
    try:
        roots = build_work_items_tree(conn)
        return {"roots": roots}
    finally:
        conn.close()


@app.get("/api/analytics")
def api_analytics(
    period: str = Query("24h", description="24h | 7d | 30d | all"),
) -> dict[str, Any]:
    """Метрики фабрики за период (read-only)."""
    p = (period or "24h").strip().lower()
    if p not in ("24h", "7d", "30d", "all"):
        raise HTTPException(
            status_code=400,
            detail="period must be one of: 24h, 7d, 30d, all",
        )
    conn = _open_ro()
    try:
        return compute_analytics(conn, p)
    finally:
        conn.close()


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    conn = _open_ro()
    try:
        by_kind = {r["kind"]: r["c"] for r in conn.execute("SELECT kind, COUNT(*) AS c FROM work_items GROUP BY kind")}
        by_status = {
            r["status"]: r["c"] for r in conn.execute("SELECT status, COUNT(*) AS c FROM work_items GROUP BY status")
        }
        runs_total = conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"]
        last_ev = conn.execute("SELECT MAX(event_time) AS t FROM event_log").fetchone()["t"]
        wi_total = conn.execute("SELECT COUNT(*) AS c FROM work_items").fetchone()["c"]
        total_visions = conn.execute(
            "SELECT COUNT(*) AS c FROM work_items WHERE kind = 'vision'"
        ).fetchone()["c"]
        total_atoms = conn.execute(
            "SELECT COUNT(*) AS c FROM work_items WHERE kind = 'atom'"
        ).fetchone()["c"]
        total_forge_runs = conn.execute(
            "SELECT COUNT(*) AS c FROM runs WHERE role = 'forge'"
        ).fetchone()["c"]
        last_forge = conn.execute(
            "SELECT MAX(finished_at) AS t FROM runs WHERE role = 'forge' AND finished_at IS NOT NULL"
        ).fetchone()["t"]
        improvements_proposed = 0
        improvements_stats: dict[str, int] = {}
        try:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS c FROM improvement_candidates GROUP BY status
                """
            ).fetchall()
            improvements_stats = {r["status"]: int(r["c"]) for r in rows}
            improvements_proposed = int(improvements_stats.get("proposed", 0))
        except sqlite3.OperationalError as e:
            _LOG.debug("improvement_candidates table unavailable in stats: %s", e)
        orch_hb = _orchestrator_heartbeat_from_conn(conn)
        try:
            wst = workers_status_payload(conn)
        except sqlite3.OperationalError as e:
            _LOG.debug("workers_status_payload fallback due to sqlite operational error: %s", e)
            wst = {"active": 0, "workers": [], "leases_total": 0}
        return {
            "active_workers": int(wst.get("active") or 0),
            "worker_leases_total": int(wst.get("leases_total") or 0),
            "workers_snapshot": wst.get("workers") or [],
            "work_items_total": wi_total,
            "by_kind": by_kind,
            "by_status": by_status,
            "runs_total": runs_total,
            "last_event_time": last_ev,
            "total_visions": int(total_visions),
            "total_atoms": int(total_atoms),
            "total_forge_runs": int(total_forge_runs),
            "last_forge_run_at": last_forge,
            "improvements_proposed": improvements_proposed,
            "improvements_stats": improvements_stats,
            **orch_hb,
        }
    finally:
        conn.close()


@app.get("/api/workers/status")
def api_workers_status() -> dict[str, Any]:
    """Активные lease в очередях (внешние worker-процессы и оркестратор)."""
    conn = _open_ro()
    try:
        return workers_status_payload(conn)
    finally:
        conn.close()


@app.get("/api/improvements")
def list_improvements() -> dict[str, Any]:
    conn = _open_ro()
    try:
        try:
            rows = conn.execute(
                """
                SELECT id, source_type, source_ref, title, description, evidence,
                       fix_target, affected_role, priority_score, status, risk_level,
                       frequency, vision_id, created_at, reviewed_at, reviewed_by
                FROM improvement_candidates
                ORDER BY priority_score DESC, created_at DESC
                """
            ).fetchall()
        except sqlite3.OperationalError as e:
            _LOG.debug("improvement_candidates table unavailable in list_improvements: %s", e)
            return {"candidates": [], "stats": {}}
        candidates = []
        for r in rows:
            candidates.append(
                {
                    "id": r["id"],
                    "source_type": r["source_type"],
                    "source_ref": r["source_ref"],
                    "title": r["title"],
                    "description": r["description"],
                    "evidence": r["evidence"],
                    "fix_target": r["fix_target"],
                    "affected_role": r["affected_role"],
                    "priority_score": float(r["priority_score"])
                    if r["priority_score"] is not None
                    else None,
                    "status": r["status"],
                    "risk_level": r["risk_level"],
                    "frequency": int(r["frequency"] or 0),
                    "vision_id": r["vision_id"],
                    "created_at": r["created_at"],
                    "reviewed_at": r["reviewed_at"],
                    "reviewed_by": r["reviewed_by"],
                }
            )
        st_rows = conn.execute(
            """
            SELECT status, COUNT(*) AS c FROM improvement_candidates GROUP BY status
            """
        ).fetchall()
        stats = {x["status"]: int(x["c"]) for x in st_rows}
        for k in ("proposed", "approved", "converted", "rejected", "expired"):
            stats.setdefault(k, 0)
        return {"candidates": candidates, "stats": stats}
    finally:
        conn.close()


@app.post("/api/improvements/{ic_id}/approve")
def approve_improvement(
    ic_id: str = FastPath(..., min_length=1, max_length=128),
    body: ImprovementReviewRequest = Body(default=ImprovementReviewRequest()),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    reviewed_by = str(body.reviewed_by or "dashboard").strip() or "dashboard"
    conn = _open_rw()
    try:
        row = conn.execute(
            "SELECT id, status FROM improvement_candidates WHERE id = ?", (ic_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        if row["status"] != "proposed":
            raise HTTPException(status_code=400, detail="only proposed can be approved")
        now = _utc_now_iso()
        conn.execute(
            """
            UPDATE improvement_candidates
            SET status = 'approved', reviewed_at = ?, reviewed_by = ?
            WHERE id = ?
            """,
            (now, reviewed_by, ic_id),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/improvements/{ic_id}/reject")
def reject_improvement(
    ic_id: str = FastPath(..., min_length=1, max_length=128),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    conn = _open_rw()
    try:
        row = conn.execute(
            "SELECT id, status FROM improvement_candidates WHERE id = ?", (ic_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        if row["status"] != "proposed":
            raise HTTPException(status_code=400, detail="only proposed can be rejected")
        conn.execute(
            "UPDATE improvement_candidates SET status = 'rejected' WHERE id = ?",
            (ic_id,),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/improvements/{ic_id}/convert")
def convert_improvement(
    ic_id: str = FastPath(..., min_length=1, max_length=128),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    from .factory_introspect import FactoryIntrospector

    conn = _open_rw()
    try:
        logger = FactoryLogger(conn)
        intro = FactoryIntrospector()
        try:
            vid = intro.convert_one(conn, ic_id, logger)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        conn.commit()
        return {"ok": True, "vision_id": vid}
    finally:
        conn.close()


@app.get("/api/queue/forge_inbox")
def queue_forge_inbox() -> dict[str, Any]:
    """Совместимость с factory-os.html (тот же контракт, что legacy ``dashboard_api``)."""
    conn = _open_ro()
    try:
        return api_forge_inbox_simple(conn)
    finally:
        conn.close()


@app.get("/api/fsm/work_item")
def fsm_work_item() -> dict[str, Any]:
    conn = _open_ro()
    try:
        return _fsm_stub(conn)
    finally:
        conn.close()


@app.get("/api/agents")
def agents_list_compat() -> dict[str, Any]:
    conn = _open_ro()
    try:
        return _agents(conn)
    finally:
        conn.close()


@app.get("/api/failure-clusters")
def failure_clusters() -> dict[str, Any]:
    return {"clusters": [], "items": []}


@app.get("/api/failures")
def failures() -> dict[str, Any]:
    """Alias for /api/failure-clusters for frontend compatibility."""
    return {"clusters": [], "items": []}


@app.get("/api/hr")
def hr_stub() -> dict[str, Any]:
    return {"policies": [], "proposals": []}


@app.get("/api/visions")
def visions() -> dict[str, Any]:
    conn = _open_ro()
    try:
        rows = conn.execute(
            "SELECT id, title, status, created_at FROM work_items WHERE kind = 'vision' ORDER BY created_at DESC"
        ).fetchall()
        items = []
        for r in rows:
            vid = r["id"]
            total_desc = conn.execute(
                "SELECT COUNT(*) AS c FROM work_items WHERE root_id = ? AND id != ?",
                (vid, vid),
            ).fetchone()["c"]
            done_desc = conn.execute(
                """
                SELECT COUNT(*) AS c FROM work_items
                WHERE root_id = ? AND id != ?
                  AND status IN ('done','cancelled','archived')
                """,
                (vid, vid),
            ).fetchone()["c"]
            atoms_total = conn.execute(
                """
                SELECT COUNT(*) AS c FROM work_items
                WHERE root_id = ? AND id != ? AND kind = 'atom'
                """,
                (vid, vid),
            ).fetchone()["c"]
            atoms_done = conn.execute(
                """
                SELECT COUNT(*) AS c FROM work_items
                WHERE root_id = ? AND id != ? AND kind = 'atom' AND status = 'done'
                """,
                (vid, vid),
            ).fetchone()["c"]
            pct = int(round((done_desc / total_desc) * 100)) if total_desc else 0
            atom_pct = int(round((atoms_done / atoms_total) * 100)) if atoms_total else 0
            items.append(
                {
                    "id": r["id"],
                    "title": r["title"],
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "progress": {
                        "total_descendants": int(total_desc),
                        "done_descendants": int(done_desc),
                        "pct": pct,
                        "atoms_total": int(atoms_total),
                        "atoms_done": int(atoms_done),
                        "atoms_pct": atom_pct,
                    },
                }
            )
        return {"items": items}
    finally:
        conn.close()


@app.post("/api/visions")
def create_vision(
    body: VisionRequest | dict[str, Any] = Body(...),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """
    Создаёт Vision и запускает planner (синхронно, MVP).
    Ответ: ``ok``, ``id``, ``title``, ``tree`` (один корень Vision с детьми), ``tree_stats``, ``reasoning``.
    """
    payload = body if isinstance(body, VisionRequest) else VisionRequest.model_validate(body)
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail={"error": "title is required"})
    description = payload.description.strip() if payload.description is not None else None

    conn: sqlite3.Connection | None = None
    try:
        from .db import init_db  # lazy import

        # ensure schema exists + seed accounts/agents on a dedicated connection
        # (если оркестратор уже держит write-транзакцию, DDL может попасть в lock;
        #  в этом случае предполагаем, что схема уже создана при startup).
        try:
            tmp = init_db(_db_path())
            tmp.close()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                raise

        conn = _open_rw()
        logger = FactoryLogger(conn)
        ops = WorkItemOps(conn, logger)
        vision_id = ops.create_vision(title, description, auto_commit=False)
        logger.log(
            EventType.VISION_CREATED,
            "work_item",
            vision_id,
            "Vision created via API",
            work_item_id=vision_id,
            actor_role=Role.CREATOR.value,
            payload={"title": title, "description": description, "source": "api"},
            tags=["api", "vision"],
        )
        out = decompose_with_planner(
            conn=conn,
            logger=logger,
            inp=PlannerInput(
                work_item_id=vision_id,
                title=title,
                description=description or "",
                kind="vision",
                current_depth=0,
                max_depth=4,
            ),
        )
        # stats: из контракта planner output
        def _stats(items) -> dict[str, int]:
            c = {"epics": 0, "stories": 0, "tasks": 0, "atoms": 0}

            def walk(it):
                k = it.kind
                if k == "epic":
                    c["epics"] += 1
                elif k == "story":
                    c["stories"] += 1
                elif k == "task":
                    c["tasks"] += 1
                elif k == "atom":
                    c["atoms"] += 1
                for ch in it.children:
                    walk(ch)

            for it in items:
                walk(it)
            return c
        stats = _stats(out.items)
        conn.commit()
        root_node = subtree_for_root_id(conn, vision_id)
        tree_payload: list[dict[str, Any]] = [root_node] if root_node else []
        return {
            "ok": True,
            "id": vision_id,
            "title": title,
            "tree": tree_payload,
            "tree_stats": stats,
            "reasoning": out.reasoning,
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception as e:
                _LOG.debug("Failed to close vision creation DB connection: %s", e, exc_info=True)


# ═══════════════════════════════════════════════════════
# VISION DECOMPOSE (Qwen)
# ═══════════════════════════════════════════════════════

def decompose_vision_endpoint(
    vision_id: str = FastPath(..., min_length=1, max_length=128),
    body: VisionRequest | dict[str, Any] = Body(...),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """
    Авто-декомпозиция Vision через Qwen.
    Возвращает иерархию: epics → stories → tasks → atoms.
    """
    payload = body if isinstance(body, VisionRequest) else VisionRequest.model_validate(body)
    title = payload.title.strip()
    description = (payload.description or "").strip()
    
    if not title:
        raise HTTPException(status_code=400, detail={"error": "title is required"})
    
    # Промпт для Qwen
    prompt = f"""
Декомпозируй задачу на иерархию Epic → Story → Task → Atom.

Vision: {title}
Описание: {description}

Верни ТОЛЬКО JSON без markdown:
{{
  "epics": [
    {{
      "title": "Epic title",
      "description": "Epic description",
      "stories": [
        {{
          "title": "Story title",
          "description": "Story description",
          "tasks": [
            {{
              "title": "Task title",
              "description": "Task description",
              "atoms": [
                {{
                  "title": "Atom title",
                  "description": "Atom description",
                  "files": ["path/to/file.py"]
                }}
              ]
            }}
          ]
        }}
      ]
    }}
  ]
}}
"""
    
    try:
        # Вызов Qwen CLI
        with _open_rw() as conn:
            logger = FactoryLogger(conn)
            am = AccountManager(conn, logger)
            result = run_qwen_cli(
                conn=conn,
                account_manager=am,
                logger=logger,
                work_item_id="api_decompose_preview",
                title=title,
                description=description,
                full_prompt=prompt,
            )
        result_text = result.stdout or result.stderr or ""
        
        # Парсинг JSON ответа
        import re
        json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
        if json_match:
            hierarchy = json.loads(json_match.group())
        else:
            hierarchy = json.loads(result_text)
        
        return {"hierarchy": hierarchy, "ok": True}
        
    except json.JSONDecodeError:
        _LOG.exception("Qwen decompose JSON error")
        raise HTTPException(status_code=500, detail={"error": "Invalid JSON from Qwen"})
    except Exception:
        _LOG.exception("Qwen decompose error")
        raise HTTPException(status_code=500, detail={"error": "Decompose failed"})


# ═══════════════════════════════════════════════════════
# CHAT (Qwen SSE)
# ═══════════════════════════════════════════════════════

async def chat_qwen_create(request: Request) -> dict[str, str]:
    """
    Создать сессию чата с Qwen.
    Возвращает chat_id для подключения к SSE потоку.
    """
    from .db import init_db

    try:
        raw = await request.json()
    except Exception:
        _LOG.exception("Invalid JSON in /api/chat/qwen request")
        raise HTTPException(status_code=400, detail="Invalid request body")
    try:
        payload = ChatCreateRequest.model_validate(raw)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors()) from e

    prompt = payload.prompt
    context = payload.context
    work_item_id = payload.work_item_id

    try:
        tmp = init_db(_db_path())
        tmp.close()
    except sqlite3.OperationalError as e:
        if "locked" not in str(e).lower():
            raise

    conn = _open_rw()
    account_manager = AccountManager(conn, FactoryLogger(conn))

    # ChatService теперь принимает db_path и создаёт свои соединения
    service = ChatService(_db_path(), account_manager)

    full_context = context or {}
    if work_item_id:
        work_item = conn.execute(
            "SELECT * FROM work_items WHERE id = ?",
            (work_item_id,)
        ).fetchone()
        if work_item:
            full_context.update({
                'work_item_id': work_item_id,
                'kind': work_item['kind'],
                'title': work_item['title'],
                'description': work_item['description'],
                'status': work_item['status']
            })

    chat_id = service.create_chat_session(prompt, full_context)
    conn.close()

    return {"chat_id": chat_id}


async def chat_qwen_stream(
    chat_id: str = FastPath(..., min_length=1, max_length=128),
) -> StreamingResponse:
    """SSE поток для чата с Qwen."""
    from starlette.responses import StreamingResponse
    from .db import init_db

    try:
        tmp = init_db(_db_path())
        tmp.close()
    except sqlite3.OperationalError as e:
        if "locked" not in str(e).lower():
            raise

    conn = _open_rw()
    account_manager = AccountManager(conn, FactoryLogger(conn))
    service = ChatService(_db_path(), account_manager)

    async def generate():
        async for chunk in service.stream_chat_response(chat_id):
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# ═══════════════════════════════════════════════════════
# QWEN FIX (Auto-fix for Forge errors)
# ═══════════════════════════════════════════════════════

def qwen_fix_endpoint(
    body: QwenFixRequest = Body(...),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """
    Запрос исправления ошибки у Qwen.
    Используется для авто-исправления Forge ошибок.
    """
    error_type = str(body.type or "unknown").strip()
    message = body.message.strip()
    context = body.context
    
    # Промпт для Qwen
    prompt = f"""
Произошла ошибка при выполнении Forge задачи.

Тип ошибки: {error_type}
Сообщение: {message}
Контекст: {json.dumps(context, indent=2)}

Проанализируй ошибку и предложи исправление.
Верни ТОЛЬКО JSON без markdown:
{{
  "suggestion": "Описание проблемы и решения",
  "files": ["path/to/file.py"],
  "changes": [
    {{
      "file": "path/to/file.py",
      "action": "modify",
      "content": "Новое содержимое файла или diff"
    }}
  ],
  "confidence": 0.95
}}
"""
    
    try:
        # Вызов Qwen CLI
        with _open_rw() as conn:
            logger = FactoryLogger(conn)
            am = AccountManager(conn, logger)
            result = run_qwen_cli(
                conn=conn,
                account_manager=am,
                logger=logger,
                work_item_id="api_fix_preview",
                title=f"Fix: {error_type}",
                description=message,
                full_prompt=prompt,
            )
        result_text = result.stdout or result.stderr or ""
        
        # Парсинг JSON ответа
        import re
        json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
        if json_match:
            fix = json.loads(json_match.group())
        else:
            fix = json.loads(result_text)
        
        return {"fix": fix, "ok": True}
        
    except json.JSONDecodeError:
        _LOG.exception("Qwen fix JSON error")
        raise HTTPException(status_code=500, detail={"error": "Invalid JSON from Qwen"})
    except Exception:
        _LOG.exception("Qwen fix error")
        raise HTTPException(status_code=500, detail={"error": "Fix failed"})



def _include_domain_routers() -> None:
    from .routers.admin_health import build_router as build_admin_health_router
    from .routers.chat import build_router as build_chat_router
    from .routers.qwen import build_router as build_qwen_router
    from .routers.work_items import build_router as build_work_items_router

    app.include_router(build_admin_health_router())
    app.include_router(build_work_items_router())
    app.include_router(build_chat_router())
    app.include_router(build_qwen_router())


_include_domain_routers()

def main(argv: list[str] | None = None) -> None:
    import uvicorn

    configure_logging(level=logging.INFO)
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(description="Factory read-only HTTP API (SQLite)")
    p.add_argument("--db", help="Путь к SQLite (иначе FACTORY_DB / factory.db)")
    p.add_argument("--host", default=API_HOST)
    p.add_argument("--port", type=int, default=API_PORT)
    args = p.parse_args(argv)
    if args.db:
        os.environ["FACTORY_DB"] = args.db
    host, port = args.host, args.port
    _LOG.info("Factory read-only API: http://%s:%s  DB=%s", host, port, _db_path())
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
