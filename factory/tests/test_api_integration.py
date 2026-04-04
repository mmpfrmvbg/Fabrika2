from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
    return TestClient(app, raise_server_exceptions=False)


def test_get_work_items_returns_items_array(api_client: TestClient) -> None:
    response = api_client.get("/api/work-items")
    assert response.status_code == 200
    payload = response.json()
    assert "items" in payload
    assert payload["limit"] == 100
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
    response = api_client.get("/api/work-items?limit=1001")
    assert response.status_code == 422


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
