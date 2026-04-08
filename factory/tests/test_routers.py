from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factory import api_server
from factory.api_server import app
from factory.db import init_db


@pytest.fixture()
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "routers_test.db"
    conn = init_db(db_path)
    conn.close()

    monkeypatch.setenv("FACTORY_DB", str(db_path))
    monkeypatch.setenv("FACTORY_API_KEY", "test-key")
    monkeypatch.setenv("FACTORY_QWEN_DRY_RUN", "1")
    api_server._RATE_LIMIT_STATE.clear()
    return TestClient(app, raise_server_exceptions=False)


def _registered_route_map() -> dict[str, set[str]]:
    route_map: dict[str, set[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        route_map.setdefault(path, set()).update(methods)
    return route_map


def test_domain_router_endpoints_are_registered() -> None:
    route_map = _registered_route_map()

    assert "GET" in route_map["/api/health"]
    assert "GET" in route_map["/api/work-items"]
    assert "GET" in route_map["/api/journal"]
    assert "POST" in route_map["/api/chat/qwen"]
    assert "POST" in route_map["/api/qwen/fix"]


@pytest.mark.parametrize(
    "endpoint",
    [
        "/api/health",
        "/api/work-items",
        "/api/journal",
        "/api/orchestrator/status",
    ],
)
def test_protected_router_endpoints_return_401_without_api_key(
    api_client: TestClient,
    endpoint: str,
) -> None:
    response = api_client.get(endpoint)
    assert response.status_code == 401


@pytest.mark.parametrize(
    "endpoint",
    [
        "/health",
        "/api/health",
        "/api/work-items",
        "/api/journal",
        "/api/orchestrator/status",
    ],
)
def test_router_endpoints_are_reachable_with_api_key(
    api_client: TestClient,
    endpoint: str,
) -> None:
    response = api_client.get(endpoint, headers={"X-API-Key": "test-key"})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "router_module",
    [
        "admin_health.py",
        "chat.py",
        "improvements.py",
        "orchestrator.py",
        "qwen.py",
        "work_items.py",
    ],
)
def test_routers_import_shared_deps_module(router_module: str) -> None:
    source = (Path(__file__).resolve().parents[1] / "routers" / router_module).read_text()

    assert "from factory import deps as srv" in source
    assert "from factory import api_server as srv" not in source
