from __future__ import annotations

from types import SimpleNamespace

from factory.dashboard_api import _dashboard_public_origin


class _DummyHandler:
    def __init__(self, host_header: str, server_address: tuple[str, int]) -> None:
        self.headers = {"Host": host_header}
        self.server = SimpleNamespace(server_address=server_address)


def test_dashboard_public_origin_uses_env_for_wildcard_bind(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_PUBLIC_HOST", "dashboard.example.com")
    h = _DummyHandler("", ("0.0.0.0", 8333))

    assert _dashboard_public_origin(h) == "http://dashboard.example.com:8333"


def test_dashboard_public_origin_uses_default_for_wildcard_bind(monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_PUBLIC_HOST", raising=False)
    h = _DummyHandler("", ("::", 8420))

    assert _dashboard_public_origin(h) == "http://127.0.0.1:8420"
