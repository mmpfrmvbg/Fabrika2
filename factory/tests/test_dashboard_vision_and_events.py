"""POST /api/visions и фильтр event_type в _events."""

from __future__ import annotations

import json

import pytest

from factory.dashboard_api import _events
from factory.dashboard_vision import api_visions_list, post_create_vision
from factory.db import init_db


def test_post_create_vision_happy(monkeypatch, tmp_path) -> None:
    db = tmp_path / "vis.db"
    monkeypatch.setenv("FACTORY_DB_PATH", str(db))
    init_db(db)
    ok, data, code = post_create_vision("Тест Vision", "описание")
    assert ok is True
    assert code == 201
    assert data.get("vision_id")
    assert data.get("status") == "draft"
    assert data.get("title") == "Тест Vision"
    assert data.get("description") == "описание"
    assert data.get("created_at")
    assert data.get("updated_at")


def test_api_visions_list_order(monkeypatch, tmp_path) -> None:
    db = tmp_path / "vis3.db"
    monkeypatch.setenv("FACTORY_DB_PATH", str(db))
    conn = init_db(db)
    conn.execute(
        """
        INSERT INTO work_items
            (id, root_id, kind, title, description, status, creator_role, owner_role, created_at, updated_at)
        VALUES
            ('vis_old', 'vis_old', 'vision', 'Old', NULL, 'draft', 'creator', 'creator',
             '2020-01-01T00:00:00.000000', '2020-01-01T00:00:00.000000'),
            ('vis_new', 'vis_new', 'vision', 'New', NULL, 'draft', 'creator', 'creator',
             '2030-01-01T00:00:00.000000', '2030-01-01T00:00:00.000000')
        """
    )
    conn.commit()
    conn.close()
    conn = init_db(db)
    out = api_visions_list(conn)
    conn.close()
    ids = [x["id"] for x in out["items"]]
    assert ids[0] == "vis_new"
    assert ids[1] == "vis_old"


def test_post_create_vision_requires_title(monkeypatch, tmp_path) -> None:
    db = tmp_path / "vis2.db"
    monkeypatch.setenv("FACTORY_DB_PATH", str(db))
    init_db(db)
    ok, data, code = post_create_vision("   ", None)
    assert ok is False
    assert code == 400


def test_events_filter_event_type(monkeypatch, tmp_path) -> None:
    db = tmp_path / "ev.db"
    monkeypatch.setenv("FACTORY_DB_PATH", str(db))
    conn = init_db(db)
    conn.execute(
        """
        INSERT INTO event_log (event_type, entity_type, entity_id, severity, message, payload)
        VALUES ('task.created', 'system', 'sys', 'info', 'a', NULL),
               ('forge.started', 'system', 'sys', 'info', 'b', NULL),
               ('comment.added', 'system', 'sys', 'info', 'c', NULL)
        """
    )
    conn.commit()
    conn.close()

    conn = init_db(db)
    out = _events(conn, 50, 0, event_type_substr="forge")
    conn.close()
    assert out["total"] == 1
    assert len(out["items"]) == 1
    assert "forge" in out["items"][0]["event_type"]
