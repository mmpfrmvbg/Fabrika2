from __future__ import annotations

from pathlib import Path


def test_api_health_uses_direct_get_connection_call() -> None:
    source = Path("factory/api_server.py").read_text(encoding="utf-8")
    marker = "def api_health()"
    start = source.index(marker)
    section = source[start : start + 1400]

    assert "conn = get_connection(DB_PATH, read_only=True)" in section
    assert "conn = _open_ro()" not in section
