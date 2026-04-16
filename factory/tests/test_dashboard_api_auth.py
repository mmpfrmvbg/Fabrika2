from __future__ import annotations

import json
import threading
from http.server import HTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from factory import dashboard_api


def _start_server() -> tuple[HTTPServer, threading.Thread, str]:
    server = HTTPServer(("127.0.0.1", 0), dashboard_api.DashboardRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = str(server.server_address[0])
    port = int(server.server_address[1])
    return server, thread, f"http://{host}:{port}"


def _post_json(
    url: str, payload: dict[str, object], token: str | None = None
) -> tuple[int, dict[str, object]]:
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("X-Internal-Token", token)
    try:
        with urlopen(req) as resp:  # noqa: S310 - local test server
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_post_returns_403_without_token_when_dashboard_token_is_set(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_TOKEN", "secret-token")

    def _fake_create_vision(title, description):
        return True, {"ok": True, "title": title, "description": description}, 200

    monkeypatch.setattr(dashboard_api, "post_create_vision", _fake_create_vision)
    server, thread, base = _start_server()
    try:
        status, body = _post_json(f"{base}/api/visions", {"title": "T"})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 403
    assert body.get("ok") is False
    assert body.get("error") == "forbidden"


def test_post_returns_200_with_valid_token(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_TOKEN", "secret-token")

    def _fake_create_vision(title, description):
        return True, {"ok": True, "title": title, "description": description}, 200

    monkeypatch.setattr(dashboard_api, "post_create_vision", _fake_create_vision)
    server, thread, base = _start_server()
    try:
        status, body = _post_json(
            f"{base}/api/visions",
            {"title": "T"},
            token="secret-token",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 200
    assert body.get("ok") is True


def test_options_uses_dashboard_cors_origin_env(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_CORS_ORIGIN", "http://example.com")
    server, thread, base = _start_server()
    try:
        req = Request(f"{base}/api/visions", method="OPTIONS")
        with urlopen(req) as resp:  # noqa: S310 - local test server
            assert resp.status == 204
            assert resp.headers.get("Access-Control-Allow-Origin") == "http://example.com"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
