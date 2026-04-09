from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from typing import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse, Response

_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMITS_PER_MINUTE = {"GET": 300, "POST": 60}
_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_STATE: dict[tuple[str, str], dict[str, float | int]] = defaultdict(dict)
_RATE_LIMIT_TTL_SECONDS = 600


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
