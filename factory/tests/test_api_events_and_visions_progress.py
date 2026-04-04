from __future__ import annotations

import sqlite3

from factory.api_server import list_events, visions
from factory.db import init_db


def test_api_events_filter_work_item_id(monkeypatch, tmp_path) -> None:
    db = tmp_path / "events_filter.db"
    monkeypatch.setenv("FACTORY_DB_PATH", str(db))
    init_db(db).close()

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        INSERT INTO event_log (event_time, event_type, entity_type, entity_id, work_item_id, severity, message, payload)
        VALUES
            (strftime('%Y-%m-%dT%H:%M:%f','now'), 'task.created', 'work_item', 'a', 'a', 'info', 'A', NULL),
            (strftime('%Y-%m-%dT%H:%M:%f','now'), 'task.created', 'work_item', 'b', 'b', 'info', 'B', NULL)
        """
    )
    conn.commit()
    conn.close()

    out = list_events(limit=10, work_item_id="a")
    assert isinstance(out, dict)
    items = out.get("items") or []
    assert len(items) >= 1
    assert all(i.get("work_item_id") == "a" for i in items)


def test_api_visions_includes_progress(monkeypatch, tmp_path) -> None:
    db = tmp_path / "visions_progress.db"
    monkeypatch.setenv("FACTORY_DB_PATH", str(db))
    init_db(db).close()

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        INSERT INTO work_items
            (id, root_id, kind, title, description, status, creator_role, owner_role, created_at, updated_at)
        VALUES
            ('vis_x', 'vis_x', 'vision', 'X', NULL, 'draft', 'creator', 'creator',
             '2030-01-01T00:00:00.000000', '2030-01-01T00:00:00.000000'),
            ('ep1', 'vis_x', 'epic', 'E1', NULL, 'done', 'planner', 'planner',
             '2030-01-01T00:00:01.000000', '2030-01-01T00:00:01.000000'),
            ('st1', 'vis_x', 'story', 'S1', NULL, 'planned', 'planner', 'planner',
             '2030-01-01T00:00:02.000000', '2030-01-01T00:00:02.000000'),
            ('a1', 'vis_x', 'atom', 'A1', NULL, 'cancelled', 'planner', 'forge',
             '2030-01-01T00:00:03.000000', '2030-01-01T00:00:03.000000')
        """
    )
    conn.commit()
    conn.close()

    out = visions()
    items = out.get("items") or []
    x = [i for i in items if i.get("id") == "vis_x"][0]
    pr = x.get("progress") or {}
    assert pr.get("total_descendants") == 3
    assert pr.get("done_descendants") == 2  # done + cancelled
    assert pr.get("pct") == 67
