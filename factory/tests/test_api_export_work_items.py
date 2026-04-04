from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factory.api_server import app
from factory.db import init_db


@pytest.fixture()
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "api_export.db"
    conn = init_db(db_path)

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, retry_count,
            created_at, updated_at
        )
        VALUES (?, NULL, ?, 'vision', 'Export Vision', 'seed', 'draft',
                'creator', 'planner', 0, 1, 0, ?, ?)
        """,
        ("wi_export_1", "wi_export_1", now, now),
    )
    conn.execute(
        """
        INSERT INTO runs (
            id, work_item_id, agent_id, role, run_type, status, started_at, finished_at
        )
        VALUES (?, ?, ?, 'forge', 'integration', 'done', datetime('now', '-2 hours'), datetime('now', '-1 hours'))
        """,
        ("run_export_1", "wi_export_1", "agent_forge"),
    )
    conn.execute(
        """
        INSERT INTO event_log (
            event_time, event_type, entity_type, entity_id, run_id, work_item_id, actor_role, severity, message
        )
        VALUES (
            datetime('now', '-5 minutes'),
            'work_item_created',
            'work_item',
            ?,
            ?,
            ?,
            'creator',
            'info',
            'export seed event'
        )
        """,
        ("wi_export_1", "run_export_1", "wi_export_1"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("FACTORY_DB", str(db_path))
    monkeypatch.setenv("FACTORY_QWEN_DRY_RUN", "1")
    return TestClient(app, raise_server_exceptions=False)


def test_export_work_items_json_returns_downloadable_payload(api_client: TestClient) -> None:
    response = api_client.get("/api/export/work-items?format=json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment;" in response.headers.get("content-disposition", "")

    payload = response.json()
    assert payload["total"] == 1
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["id"] == "wi_export_1"
    assert item["runs"][0]["id"] == "run_export_1"
    assert item["events"][0]["message"] == "export seed event"


def test_export_work_items_csv_returns_downloadable_csv(api_client: TestClient) -> None:
    response = api_client.get("/api/export/work-items?format=csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment;" in response.headers.get("content-disposition", "")

    content = response.text
    assert "work_item_id,kind,status,title,runs_json,events_json" in content
    assert "wi_export_1" in content
    assert "run_export_1" in content
    assert "export seed event" in content


def test_export_work_items_rejects_unknown_format(api_client: TestClient) -> None:
    response = api_client.get("/api/export/work-items?format=xml")
    assert response.status_code == 422
