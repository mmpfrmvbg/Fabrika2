from __future__ import annotations

import inspect

from factory.routers import work_items


class _DummyConn:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _DummyQueryConn(_DummyConn):
    def __init__(self, row: dict[str, object]) -> None:
        super().__init__()
        self._row = row

    def execute(self, _sql: str, _params: tuple[object, ...] = ()):
        class _Cursor:
            def __init__(self, row: dict[str, object]) -> None:
                self._row = row

            def fetchone(self):
                return self._row

        return _Cursor(self._row)

    def commit(self) -> None:
        return None


def test_no_api_server_loader_function() -> None:
    source = inspect.getsource(work_items)
    assert "def _api_server(" not in source
    assert "get_connection(DB_PATH, read_only=True)" in source
    assert "get_connection(DB_PATH)" in source


def test_list_work_items_uses_get_connection_read_only(monkeypatch) -> None:
    conn = _DummyConn()
    called: dict[str, object] = {}

    def _fake_get_connection(db_path, *, read_only=False):
        called["db_path"] = db_path
        called["read_only"] = read_only
        return conn

    monkeypatch.setattr(work_items, "get_connection", _fake_get_connection)
    monkeypatch.setattr(
        work_items,
        "get_work_items_paginated",
        lambda _conn, **_kwargs: {"items": [], "total": 0},
    )

    out = work_items.list_work_items(limit=10, offset=0)

    assert out == {"items": [], "total": 0}
    assert called["db_path"] == work_items.DB_PATH
    assert called["read_only"] is True
    assert conn.closed is True


def test_create_work_item_legacy_uses_rw_connection(monkeypatch) -> None:
    row = {
        "id": "wi_1",
        "kind": "task",
        "title": "T",
    }
    conn = _DummyQueryConn(row)
    called: dict[str, object] = {}

    def _fake_get_connection(db_path, *, read_only=False):
        called["db_path"] = db_path
        called["read_only"] = read_only
        return conn

    monkeypatch.setattr(work_items, "get_connection", _fake_get_connection)
    monkeypatch.setattr(work_items, "gen_id", lambda _prefix: "wi_1")

    out = work_items.create_work_item_legacy(
        body={
            "kind": "task",
            "title": "T",
            "description": None,
            "parent_id": None,
            "priority": 1,
            "deadline_at": None,
            "idempotency_key": None,
        }
    )

    assert out["ok"] is True
    assert out["work_item"]["id"] == "wi_1"
    assert called["db_path"] == work_items.DB_PATH
    assert called["read_only"] is False
    assert conn.closed is True
