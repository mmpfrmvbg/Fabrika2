from __future__ import annotations

import pytest
from starlette.requests import Request

from factory import middleware


def _request(*, client_host: str | None, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/health",
        "headers": headers or [],
    }
    if client_host is not None:
        scope["client"] = (client_host, 12345)
    return Request(scope)


def test_client_ip_uses_direct_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FACTORY_TRUSTED_PROXY", raising=False)
    request = _request(client_host="198.51.100.1")
    assert middleware._client_ip(request) == "198.51.100.1"


def test_client_ip_uses_forwarded_for_only_for_trusted_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACTORY_TRUSTED_PROXY", "127.0.0.1")
    request = _request(
        client_host="127.0.0.1",
        headers=[(b"x-forwarded-for", b"203.0.113.11, 10.0.0.1")],
    )
    assert middleware._client_ip(request) == "203.0.113.11"


def test_client_ip_ignores_spoofed_forwarded_for(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACTORY_TRUSTED_PROXY", "127.0.0.1")
    request = _request(
        client_host="198.51.100.1",
        headers=[(b"x-forwarded-for", b"203.0.113.99")],
    )
    assert middleware._client_ip(request) == "198.51.100.1"


def test_rate_limit_meta_counts_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    original_limits = dict(middleware._RATE_LIMITS_PER_MINUTE)
    middleware._RATE_LIMIT_STATE.clear()
    middleware._RATE_LIMITS_PER_MINUTE["GET"] = 2

    now = 1_000_000.0
    monkeypatch.setattr(middleware.time, "time", lambda: now)
    try:
        first = middleware._rate_limit_meta("GET", "198.51.100.10")
        second = middleware._rate_limit_meta("GET", "198.51.100.10")
        third = middleware._rate_limit_meta("GET", "198.51.100.10")
    finally:
        middleware._RATE_LIMITS_PER_MINUTE.clear()
        middleware._RATE_LIMITS_PER_MINUTE.update(original_limits)

    assert first == {"limit": 2, "remaining": 1, "retry_after": 60, "is_limited": 0}
    assert second == {"limit": 2, "remaining": 0, "retry_after": 60, "is_limited": 0}
    assert third == {"limit": 2, "remaining": 0, "retry_after": 60, "is_limited": 1}


def test_rate_limit_meta_evicts_ttl_expired_state(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 2_000_000.0
    middleware._RATE_LIMIT_STATE.clear()
    middleware._RATE_LIMIT_STATE[("GET", "stale")] = {
        "window_start": now - 700.0,
        "count": 1,
        "last_access": now - 700.0,
    }
    middleware._RATE_LIMIT_STATE[("GET", "fresh")] = {
        "window_start": now - 1.0,
        "count": 1,
        "last_access": now - 1.0,
    }
    monkeypatch.setattr(middleware.time, "time", lambda: now)

    middleware._rate_limit_meta("GET", "127.0.0.1")

    assert ("GET", "stale") not in middleware._RATE_LIMIT_STATE
    assert ("GET", "fresh") in middleware._RATE_LIMIT_STATE
