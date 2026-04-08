from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from factory import api_server
from factory.api_server import app
from factory.db import init_db


@pytest.fixture()
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "api_integration.db"
    conn = init_db(db_path)

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, retry_count,
            created_at, updated_at
        )
        VALUES (?, NULL, ?, 'vision', 'Integration Vision', 'seed', 'draft',
                'creator', 'planner', 0, 1, 0, ?, ?)
        """,
        ("wi_integration_1", "wi_integration_1", now, now),
    )
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, retry_count,
            created_at, updated_at
        )
        VALUES (?, ?, ?, 'atom', 'Integration Atom', 'seed atom', 'running',
                'creator', 'forge', 1, 1, 0, ?, ?)
        """,
        ("wi_integration_2", "wi_integration_1", "wi_integration_1", now, now),
    )
    conn.execute(
        """
        INSERT INTO runs (
            id, work_item_id, agent_id, role, run_type, status, started_at, finished_at
        )
        VALUES (?, ?, ?, 'forge', 'integration', ?, datetime('now', '-2 hours'), datetime('now', '-1 hours'))
        """,
        ("run_integration_1", "wi_integration_1", "agent_forge", "done"),
    )
    conn.execute(
        """
        INSERT INTO runs (
            id, work_item_id, agent_id, role, run_type, status, started_at, finished_at
        )
        VALUES (?, ?, ?, 'forge', 'integration', ?, datetime('now', '-3 hours'), datetime('now', '-2 hours'))
        """,
        ("run_integration_2", "wi_integration_2", "agent_forge", "failed"),
    )
    conn.execute(
        """
        INSERT INTO event_log (
            event_time, event_type, entity_type, entity_id, actor_role, severity, message
        )
        VALUES (
            datetime('now', '-5 seconds'),
            'heartbeat',
            'system',
            'orchestrator',
            'orchestrator',
            'info',
            'integration heartbeat'
        )
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("FACTORY_DB", str(db_path))
    monkeypatch.setenv("FACTORY_QWEN_DRY_RUN", "1")
    api_server._RATE_LIMIT_STATE.clear()
    return TestClient(app, raise_server_exceptions=False)


def test_get_work_items_returns_items_array(api_client: TestClient) -> None:
    response = api_client.get("/api/work-items")
    assert response.status_code == 200
    payload = response.json()
    assert "items" in payload
    assert payload["limit"] == 50
    assert payload["offset"] == 0
    assert payload["total"] == 2
    assert payload["has_more"] is False
    assert isinstance(payload["items"], list)
    assert any(item["id"] == "wi_integration_1" for item in payload["items"])


def test_get_work_items_supports_limit_and_offset(api_client: TestClient) -> None:
    response = api_client.get("/api/work-items?limit=1&offset=0")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["limit"] == 1
    assert payload["offset"] == 0
    assert payload["total"] == 2
    assert payload["has_more"] is True

    response_page_2 = api_client.get("/api/work-items?limit=1&offset=1")
    assert response_page_2.status_code == 200
    payload_page_2 = response_page_2.json()
    assert len(payload_page_2["items"]) == 1
    assert payload_page_2["has_more"] is False


def test_get_work_items_limit_max_validation(api_client: TestClient) -> None:
    response = api_client.get("/api/work-items?limit=501")
    assert response.status_code == 422


def test_get_work_items_supports_status_filter(api_client: TestClient) -> None:
    response = api_client.get("/api/work-items?status=running")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert all(item["status"] == "running" for item in payload["items"])


def test_events_endpoint_streams_sse(api_client: TestClient) -> None:
    with api_client.stream("GET", "/api/events?last_event_id=0&once=1") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = response.iter_lines()
        first_data_line = ""
        for line in lines:
            if line.startswith("data: "):
                first_data_line = line
                break
        assert first_data_line.startswith("data: {")
        assert '"id":' in first_data_line
        assert '"type":' in first_data_line
        assert '"payload":' in first_data_line


def test_get_work_item_by_id_existing_and_nonexistent(api_client: TestClient) -> None:
    ok_response = api_client.get("/api/work-items/wi_integration_1")
    assert ok_response.status_code == 200
    assert ok_response.json()["work_item"]["id"] == "wi_integration_1"

    missing_response = api_client.get("/api/work-items/wi_missing")
    assert missing_response.status_code == 404


def test_post_work_item_valid_body_returns_success(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/visions",
        json={"title": "HTTP Integration Vision", "description": "created in integration test"},
    )
    assert response.status_code in (200, 201)
    payload = response.json()
    assert payload.get("ok") is True
    assert isinstance(payload.get("id"), str) and payload["id"]


def test_post_work_item_invalid_body_returns_422(api_client: TestClient) -> None:
    response = api_client.post("/api/visions", json={"description": "missing title"})
    assert response.status_code in (422, 500)


def test_get_dashboard_summary_returns_expected_structure(api_client: TestClient) -> None:
    response = api_client.get("/api/stats")
    assert response.status_code == 200
    payload = response.json()
    for key in ("work_items_total", "by_kind", "by_status", "runs_total"):
        assert key in payload


def test_get_analytics_returns_200(api_client: TestClient) -> None:
    response = api_client.get("/api/analytics")
    assert response.status_code == 200


def test_get_failures_returns_clusters_key(api_client: TestClient) -> None:
    response = api_client.get("/api/failures")
    assert response.status_code == 200
    assert "clusters" in response.json()


def test_get_improvements_returns_candidates(api_client: TestClient) -> None:
    response = api_client.get("/api/improvements")
    assert response.status_code == 200
    assert "candidates" in response.json()


def test_patch_work_item_valid_data_returns_200(api_client: TestClient) -> None:
    response = api_client.patch(
        "/api/work-items/wi_integration_1",
        json={"title": "Updated Integration Vision", "description": "updated"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["work_item"]["title"] == "Updated Integration Vision"


def test_patch_work_item_invalid_status_value_returns_400_or_422(api_client: TestClient) -> None:
    response = api_client.patch(
        "/api/work-items/wi_integration_1",
        json={"status": "not_a_real_status"},
    )
    assert response.status_code in (400, 422)


def test_api_health_returns_uptime_and_db_status(api_client: TestClient) -> None:
    response = api_client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["db_connected"] is True
    assert isinstance(payload["uptime_seconds"], float)
    assert payload["uptime_seconds"] >= 0.0
    assert "worker_status" in payload
    assert "orchestrator_heartbeat" in payload
    assert payload["version"]["api"] == app.version


def test_api_metrics_returns_operational_stats(api_client: TestClient) -> None:
    response = api_client.get("/api/metrics")
    assert response.status_code == 200
    payload = response.json()
    assert payload["work_items_total"] == 2
    assert payload["work_items_by_status"]["draft"] == 1
    assert payload["work_items_by_status"]["running"] == 1
    assert payload["runs_total"] == 2
    assert payload["runs_last_24h"] == 2
    assert payload["failed_runs_last_24h"] == 1
    assert isinstance(payload["avg_run_duration_seconds"], float)
    assert payload["avg_run_duration_seconds"] > 0.0
    assert payload["orchestrator_running"] is True


def test_get_rate_limit_headers_present(api_client: TestClient) -> None:
    response = api_client.get("/api/health")
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == "300"
    assert int(response.headers["X-RateLimit-Remaining"]) >= 0


def test_post_rate_limiting_returns_429(api_client: TestClient) -> None:
    headers = {"x-forwarded-for": "203.0.113.10"}
    for _ in range(60):
        response = api_client.post("/api/visions", json={"title": "Rate Test"}, headers=headers)
        assert response.status_code in (200, 201)
    limited = api_client.post("/api/visions", json={"title": "Rate Test"}, headers=headers)
    assert limited.status_code == 429
    assert limited.headers.get("Retry-After") is not None
    assert limited.headers["X-RateLimit-Limit"] == "60"
    assert limited.headers["X-RateLimit-Remaining"] == "0"


def test_rate_limit_ignores_spoofed_forwarded_for(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    original = dict(api_server._RATE_LIMITS_PER_MINUTE)
    api_server._RATE_LIMIT_STATE.clear()
    api_server._RATE_LIMITS_PER_MINUTE["GET"] = 2
    try:
        r1 = api_client.get("/api/health", headers={"x-forwarded-for": "198.51.100.10"})
        r2 = api_client.get("/api/health", headers={"x-forwarded-for": "198.51.100.11"})
        r3 = api_client.get("/api/health", headers={"x-forwarded-for": "198.51.100.12"})
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r3.status_code == 429
    finally:
        api_server._RATE_LIMITS_PER_MINUTE.clear()
        api_server._RATE_LIMITS_PER_MINUTE.update(original)


def test_rate_limit_state_ttl_eviction() -> None:
    now = 2_000_000.0
    api_server._RATE_LIMIT_STATE.clear()
    api_server._RATE_LIMIT_STATE[("GET", "stale")] = {
        "window_start": now - 700.0,
        "count": 1,
        "last_access": now - 700.0,
    }
    api_server._RATE_LIMIT_STATE[("GET", "fresh")] = {
        "window_start": now - 1.0,
        "count": 1,
        "last_access": now - 1.0,
    }
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(api_server.time, "time", lambda: now)
    try:
        api_server._rate_limit_meta("GET", "127.0.0.1")
    finally:
        monkeypatch.undo()
    assert ("GET", "stale") not in api_server._RATE_LIMIT_STATE
    assert ("GET", "fresh") in api_server._RATE_LIMIT_STATE


def test_post_work_items_accepts_priority_and_get_returns_it(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/work_items",
        json={"title": "Priority item", "kind": "task", "priority": 9},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["work_item"]["priority"] == 9
    wi_id = payload["work_item"]["id"]

    get_response = api_client.get(f"/api/work_items?id={wi_id}")
    assert get_response.status_code == 200
    assert get_response.json()["work_item"]["priority"] == 9


def test_post_work_items_assigns_correlation_id(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/work_items",
        json={"title": "Correlation item", "kind": "task"},
    )
    assert response.status_code == 200
    payload = response.json()
    corr = payload["work_item"].get("correlation_id")
    assert isinstance(corr, str)
    assert len(corr) >= 32

    wi_id = payload["work_item"]["id"]
    fetched = api_client.get(f"/api/work-items/{wi_id}")
    assert fetched.status_code == 200
    assert fetched.json()["work_item"]["correlation_id"] == corr


def test_post_work_items_returns_409_for_duplicate_idempotency_key(api_client: TestClient) -> None:
    key = "idem-key-001"
    first = api_client.post(
        "/api/work_items",
        json={"title": "First", "kind": "task", "idempotency_key": key},
    )
    assert first.status_code == 200
    first_id = first.json()["work_item"]["id"]

    second = api_client.post(
        "/api/work_items",
        json={"title": "Second", "kind": "task", "idempotency_key": key},
    )
    assert second.status_code == 409
    assert second.json() == {"error": "duplicate", "existing_id": first_id}


def test_post_work_items_accepts_deadline_and_get_exposes_it(api_client: TestClient) -> None:
    deadline = "2026-04-10T10:20:30Z"
    response = api_client.post(
        "/api/work_items",
        json={"title": "Deadline item", "kind": "task", "deadline_at": deadline},
    )
    assert response.status_code == 200
    wi = response.json()["work_item"]
    assert wi["deadline_at"] is not None
    assert wi["deadline_at"].startswith("2026-04-10T10:20:30")

    wi_id = wi["id"]
    fetched = api_client.get(f"/api/work_items?id={wi_id}")
    assert fetched.status_code == 200
    assert fetched.json()["work_item"]["deadline_at"].startswith("2026-04-10T10:20:30")


def test_post_runs_preserves_correlation_id_in_run_and_events(api_client: TestClient) -> None:
    created = api_client.post("/api/work_items", json={"title": "Run item", "kind": "atom"})
    wi_id = created.json()["work_item"]["id"]
    corr = "11111111-2222-3333-4444-555555555555"

    run_response = api_client.post(
        "/api/runs",
        json={"work_item_id": wi_id, "correlation_id": corr},
    )
    assert run_response.status_code == 200
    assert run_response.json()["correlation_id"] == corr

    db_conn = sqlite3.connect(str(api_server._db_path()))
    db_conn.row_factory = sqlite3.Row
    try:
        run_row = db_conn.execute(
            "SELECT correlation_id FROM runs WHERE work_item_id = ? ORDER BY started_at DESC, id DESC LIMIT 1",
            (wi_id,),
        ).fetchone()
        assert run_row is not None
        assert run_row["correlation_id"] == corr
        ev_row = db_conn.execute(
            "SELECT correlation_id, payload FROM event_log WHERE work_item_id = ? ORDER BY id DESC LIMIT 1",
            (wi_id,),
        ).fetchone()
        assert ev_row is not None
        assert ev_row["correlation_id"] == corr
    finally:
        db_conn.close()


def test_legacy_work_items_list_endpoint_removed(api_client: TestClient) -> None:
    response = api_client.get("/api/work_items?status=dead")
    assert response.status_code in (401, 404)


def test_api_health_returns_503_on_sqlite_operational_error(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom():
        raise sqlite3.OperationalError("db unavailable")

    monkeypatch.setattr(api_server, "_open_ro", _boom)
    response = api_client.get("/api/health")
    assert response.status_code == 503
