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
import logging
import os
import sqlite3
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from .composition import wire
from .config import (API_HOST, API_PORT, ORCHESTRATOR_TICK_INTERVAL_SECONDS,
                     get_factory_api_key, load_dotenv, resolve_db_path)
from .db import DB_PATH, _db_path, get_async_connection, get_connection
from .logging import FactoryLogger
from .logging_config import configure_logging
from .middleware import (_RATE_LIMIT_STATE, _RATE_LIMITS_PER_MINUTE,
                         _rate_limit_meta, rate_limit_middleware)
from .orchestrator_thread import _OrchestratorThread

load_dotenv()

# Глобальный logger для endpoint (создаётся при первом использовании)
_logger: FactoryLogger | None = None
_LOG = logging.getLogger("factory.api_server")
_API_STARTED_AT_MONOTONIC = time.monotonic()


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


def _open_ro() -> sqlite3.Connection:
    """Compatibility helper for legacy router imports."""
    return get_connection(resolve_db_path(), read_only=True)


def _open_rw() -> sqlite3.Connection:
    """Compatibility helper for legacy router imports."""
    return get_connection(resolve_db_path(), read_only=False)


_orch_thread = _OrchestratorThread()


def _embedded_orchestrator_enabled() -> bool:
    raw = (os.environ.get("FACTORY_EMBEDDED_ORCHESTRATOR") or "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


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
    if _embedded_orchestrator_enabled():
        _orch_thread.start()
    try:
        yield
    finally:
        if _embedded_orchestrator_enabled():
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


async def api_health() -> dict[str, Any]:
    uptime_seconds = max(0.0, time.monotonic() - _API_STARTED_AT_MONOTONIC)
    db_connected = False
    worker_status: dict[str, Any] = {"active": 0, "workers": [], "leases_total": 0}
    orchestrator_heartbeat: dict[str, Any] = {
        "orchestrator_last_event_time": None,
        "orchestrator_seconds_since_last_event": None,
        "orchestrator_heartbeat_state": "none",
    }
    try:
        conn = await get_async_connection(DB_PATH, read_only=True)
        try:
            cursor = await conn.execute("SELECT 1")
            await cursor.fetchone()
            db_connected = True
            now_iso = datetime.now(timezone.utc).isoformat()
            worker_rows = await (
                await conn.execute(
                    """
                    SELECT queue_name, lease_owner, lease_until, attempts, work_item_id
                    FROM work_item_queue
                    WHERE lease_owner IS NOT NULL AND lease_until > ?
                    ORDER BY lease_until DESC
                    """,
                    (now_iso,),
                )
            ).fetchall()
            leases_total_row = await (
                await conn.execute(
                    "SELECT COUNT(*) AS c FROM work_item_queue WHERE lease_owner IS NOT NULL"
                )
            ).fetchone()
            worker_status = {
                "active": len(list(worker_rows)),
                "workers": [
                    {
                        "queue": row["queue_name"],
                        "owner": row["lease_owner"],
                        "lease_until": row["lease_until"],
                        "attempts": int(row["attempts"] or 0),
                        "work_item_id": row["work_item_id"],
                    }
                    for row in worker_rows
                ],
                "leases_total": int(leases_total_row["c"] if leases_total_row else 0),
            }

            heartbeat_row = await (
                await conn.execute(
                    """
                    SELECT MAX(event_time) AS t FROM event_log
                    WHERE LOWER(COALESCE(actor_role, '')) = 'orchestrator'
                    """
                )
            ).fetchone()
            ts = heartbeat_row["t"] if heartbeat_row else None
            dt = None
            if ts:
                s = str(ts).strip()
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                try:
                    dt = datetime.fromisoformat(s)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    dt = None
            if dt:
                sec = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
                if sec < 30.0:
                    hb_state = "active"
                elif sec < 60.0:
                    hb_state = "warn"
                else:
                    hb_state = "stale"
                orchestrator_heartbeat = {
                    "orchestrator_last_event_time": ts,
                    "orchestrator_seconds_since_last_event": sec,
                    "orchestrator_heartbeat_state": hb_state,
                }
        finally:
            await conn.close()
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
    if request.url.path in {"/api/health", "/health"}:
        return await call_next(request)
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


app.middleware("http")(rate_limit_middleware)


def orchestrator_status() -> dict[str, Any]:
    """Legacy compatibility export: delegated to orchestrator router."""
    from .routers.orchestrator import orchestrator_status as _orchestrator_status

    return _orchestrator_status()


def orchestrator_tick() -> dict[str, Any]:
    """Legacy compatibility export: delegated to orchestrator router."""
    from .routers.orchestrator import orchestrator_tick as _orchestrator_tick

    return _orchestrator_tick()


def orchestrator_health() -> dict[str, Any]:
    """Legacy compatibility export: delegated to orchestrator router."""
    from .routers.orchestrator import orchestrator_health as _orchestrator_health

    return _orchestrator_health()


def stats() -> dict[str, Any]:
    """Legacy compatibility export: delegated to analytics router."""
    from .routers.analytics import stats as _stats

    return _stats()


def api_analytics(period: str = "24h") -> dict[str, Any]:
    """Legacy compatibility export: delegated to analytics router."""
    from .routers.analytics import api_analytics as _api_analytics

    return _api_analytics(period=period)


def list_events(
    limit: int = 10,
    work_item_id: str | None = None,
    event_type: str | None = None,
    stream: bool = False,
) -> Any:
    """Legacy compatibility export: delegated to runs router."""
    from .routers.runs import list_events as _list_events

    return _list_events(
        limit=limit,
        work_item_id=work_item_id,
        event_type=event_type,
        stream=stream,
    )































































def _include_domain_routers() -> None:
    from .routers.admin_health import build_router as build_admin_health_router
    from .routers.agents import build_agents_router
    from .routers.analytics import build_router as build_analytics_router
    from .routers.chat import build_router as build_chat_router
    from .routers.improvements import build_router as build_improvements_router
    from .routers.journal import build_router as build_journal_router
    from .routers.orchestrator import build_router as build_orchestrator_router
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
    app.include_router(build_agents_router())


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
