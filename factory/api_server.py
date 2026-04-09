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
import threading
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from .composition import wire
from .config import (API_HOST, API_PORT, ORCHESTRATOR_TICK_INTERVAL_SECONDS,
                     get_factory_api_key, load_dotenv)
from .db import _db_path, _open_ro, get_connection
from .logging import FactoryLogger
from .logging_config import configure_logging
from .models import EventType
from .orchestrator_thread import _OrchestratorThread
from .workers_status import workers_status_payload

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
