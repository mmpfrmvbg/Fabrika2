from __future__ import annotations

import sqlite3
from pathlib import Path

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
    db_path = Path("test_rate_limit_meta_counts_and_limits.db")
    monkeypatch.setattr(middleware, "DB_PATH", db_path)

    now = 1_000_000.0
    monkeypatch.setattr(middleware.time, "time", lambda: now)
    try:
        first = middleware._rate_limit_meta("GET", "198.51.100.10")
        second = middleware._rate_limit_meta("GET", "198.51.100.10")
        third = middleware._rate_limit_meta("GET", "198.51.100.10")
    finally:
        middleware._RATE_LIMITS_PER_MINUTE.clear()
        middleware._RATE_LIMITS_PER_MINUTE.update(original_limits)
        if db_path.exists():
            db_path.unlink()

    assert first["limit"] == 2 and first["remaining"] == 1 and first["is_limited"] == 0
    assert second["limit"] == 2 and second["remaining"] == 0 and second["is_limited"] == 0
    assert third["limit"] == 2 and third["remaining"] == 0 and third["is_limited"] == 1
    assert 0 < first["retry_after"] <= 60
    assert 0 < second["retry_after"] <= 60
    assert 0 < third["retry_after"] <= 60


def test_rate_limit_meta_evicts_ttl_expired_state(monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = Path("test_rate_limit_meta_evicts_ttl_expired_state.db")
    monkeypatch.setattr(middleware, "DB_PATH", db_path)
    now = 2_000_000
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS rate_limit_log (key TEXT, window_start INTEGER, count INTEGER, PRIMARY KEY (key, window_start))"
        )
        conn.execute(
            "INSERT OR REPLACE INTO rate_limit_log(key, window_start, count) VALUES (?, ?, ?)",
            ("stale", now - 120, 1),
        )
        conn.execute(
            "INSERT OR REPLACE INTO rate_limit_log(key, window_start, count) VALUES (?, ?, ?)",
            ("fresh", now - 60, 1),
        )
    monkeypatch.setattr(middleware.time, "time", lambda: now)

    middleware._rate_limit_meta("GET", "127.0.0.1")

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT key, window_start FROM rate_limit_log ORDER BY key").fetchall()
    assert ("stale", now - 120) not in rows
    assert ("fresh", now - 60) in rows
    if db_path.exists():
        db_path.unlink()
