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
from pydantic import ValidationError

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
from .dashboard_api_read import get_work_items_paginated
from .analytics_api import compute_analytics
from .dashboard_unified_journal import JournalFilters, api_journal_query
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
from .qwen_cli_runner import run_qwen_cli
from .chat_service import ChatService
from .logging_config import configure_logging
from .schemas import (
    BulkArchiveRequest,
    ChatCreateRequest,
    QwenFixRequest,
    RunCreateRequest,
    WorkItemCreateRequest,
    WorkItemPatchRequest,
)

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
            if conn is not None:
                _logger = FactoryLogger(conn)
            else:
                tmp_conn = get_connection(_db_path())
                _logger = FactoryLogger(tmp_conn)
                _logger.take_connection_ownership()
        except Exception as e:
            # Fallback: logger без connection
            _LOG.debug("Falling back to FactoryLogger(None): %s", e, exc_info=True)
            _logger = FactoryLogger(None)
    return _logger


def _close_logger() -> None:
    global _logger
    if _logger is None:
        return
    try:
        _logger.close()
    except Exception as e:
        _LOG.debug("Failed to close API logger: %s", e, exc_info=True)
    finally:
        _logger = None


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
        _close_logger()


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
            from .routers.orchestrator import _orchestrator_heartbeat_from_conn

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


















_EDITABLE_STATUSES = frozenset(
    {"draft", "planned", "ready_for_judge", "judge_rejected"}
)







































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


def api_workers_status() -> dict[str, Any]:
    """Активные lease в очередях (внешние worker-процессы и оркестратор)."""
    conn = _open_ro()
    try:
        return workers_status_payload(conn)
    finally:
        pass



def judgements(
    work_item_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    conn = _open_ro()
    try:
        return {"items": _load_judgements_items(conn, work_item_id, limit)}
    finally:
        conn.close()



def queue_forge_inbox() -> dict[str, Any]:
    """Совместимость с factory-os.html (тот же контракт, что legacy ``dashboard_api``)."""
    conn = _open_ro()
    try:
        return api_forge_inbox_simple(conn)
    finally:
        pass

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


def fsm_work_item() -> dict[str, Any]:
    conn = _open_ro()
    try:
        return _fsm_stub(conn)
    finally:
        pass

def tree() -> dict[str, Any]:
    conn = _open_ro()
    try:
        roots = build_work_items_tree(conn)
        return {"roots": roots}
    finally:
        conn.close()


def agents_list_compat() -> dict[str, Any]:
    conn = _open_ro()
    try:
        return _agents(conn)
    finally:
        conn.close()


def failure_clusters() -> dict[str, Any]:
    return {"clusters": [], "items": []}


def failures() -> dict[str, Any]:
    """Alias for /api/failure-clusters for frontend compatibility."""
    return {"clusters": [], "items": []}


def hr_stub() -> dict[str, Any]:
    return {"policies": [], "proposals": []}


def _include_domain_routers() -> None:
    from .routers.analytics import build_router as build_analytics_router
    from .routers.admin_health import build_router as build_admin_health_router
    from .routers.chat import build_router as build_chat_router
    from .routers.journal import build_router as build_journal_router
    from .routers.orchestrator import build_router as build_orchestrator_router
    from .routers.improvements import build_router as build_improvements_router
    from .routers.qwen import build_router as build_qwen_router
    from .routers.runs import build_router as build_runs_router
    from .routers.visions import build_router as build_visions_router
    from .routers.work_items import build_router as build_work_items_router

    app.include_router(build_admin_health_router())
    app.include_router(build_analytics_router())
    app.include_router(build_work_items_router())
    app.include_router(build_runs_router())
    app.include_router(build_journal_router())
    app.include_router(build_orchestrator_router())
    app.include_router(build_improvements_router())
    app.include_router(build_visions_router())
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
