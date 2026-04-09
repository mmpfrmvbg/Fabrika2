from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from factory.db import init_db
from factory.routers import work_items as work_items_router


def test_export_work_items_json_reads_event_payload_column(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "api_export_payload.db"
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
        ("wi_export_payload", "wi_export_payload", now, now),
    )
    conn.execute(
        """
        INSERT INTO event_log (
            event_time, event_type, entity_type, entity_id, work_item_id,
            actor_role, severity, message, payload
        )
        VALUES (
            datetime('now', '-1 minutes'),
            'work_item_created',
            'work_item',
            ?,
            ?,
            'creator',
            'info',
            'export payload event',
            ?
        )
        """,
        ("wi_export_payload", "wi_export_payload", json.dumps({"source": "test"})),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(work_items_router, "DB_PATH", db_path)

    response = work_items_router.export_work_items(format="json")

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["total"] == 1

    events = payload["items"][0]["events"]
    assert len(events) == 1
    assert events[0]["message"] == "export payload event"
    assert events[0]["payload"] == json.dumps({"source": "test"})
