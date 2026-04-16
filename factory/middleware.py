from __future__ import annotations

import os
import time
from typing import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .config import DB_PATH
from .db import get_connection

_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMITS_PER_MINUTE = {"GET": 300, "POST": 60}
_RATE_LIMIT_STATE: dict[tuple[str, str], dict[str, float | int]] = {}


def _ensure_rate_limit_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rate_limit_log (
            key TEXT NOT NULL,
            window_start INTEGER NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (key, window_start)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_rate_limit_log_window
        ON rate_limit_log(window_start)
        """
    )


def _rate_limit_key(request: Request) -> str:
    api_key = (request.headers.get("X-API-Key") or "").strip() or "anonymous"
    return f"{api_key}:{request.method.upper()}"


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


def _rate_limit_meta(method: str, key: str) -> dict[str, int]:
    limit = _RATE_LIMITS_PER_MINUTE.get(method.upper())
    if not limit:
        return {"limit": 0, "remaining": 0, "retry_after": 0, "is_limited": 0}

    now = int(time.time())
    window_start = now - (now % _RATE_LIMIT_WINDOW_SECONDS)
    prev_window = window_start - _RATE_LIMIT_WINDOW_SECONDS

    with get_connection(DB_PATH) as conn:
        _ensure_rate_limit_schema(conn)
        conn.execute("DELETE FROM rate_limit_log WHERE window_start < ?", (prev_window,))
        conn.execute(
            """
            INSERT INTO rate_limit_log (key, window_start, count)
            VALUES (?, ?, 1)
            ON CONFLICT(key, window_start) DO UPDATE SET count = count + 1
            """,
            (key, window_start),
        )
        row = conn.execute(
            "SELECT count FROM rate_limit_log WHERE key = ? AND window_start = ?",
            (key, window_start),
        ).fetchone()
        count = int(row["count"] if row else 0)

    ttl = float(_RATE_LIMIT_WINDOW_SECONDS * 10)
    now_f = float(now)
    stale_keys = [
        k for k, st in _RATE_LIMIT_STATE.items()
        if now_f - float(st.get("last_access", 0.0)) > ttl
    ]
    for stale in stale_keys:
        _RATE_LIMIT_STATE.pop(stale, None)
    _RATE_LIMIT_STATE[(method.upper(), key)] = {
        "window_start": float(window_start),
        "count": count,
        "last_access": now_f,
    }

    elapsed = now - window_start
    remaining = max(0, limit - count)
    retry_after = max(0, _RATE_LIMIT_WINDOW_SECONDS - elapsed)
    is_limited = 1 if count > limit else 0
    return {
        "limit": limit,
        "remaining": remaining,
        "retry_after": retry_after,
        "is_limited": is_limited,
    }


async def rate_limit_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    _client_ip(request)
    meta = _rate_limit_meta(request.method, _rate_limit_key(request))
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
