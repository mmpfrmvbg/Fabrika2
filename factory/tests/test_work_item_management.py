"""API: cancel / archive / delete / PATCH work_items (creator management)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from factory import api_server
from factory.api_server import app
from factory.routers import work_items as work_items_router
from factory.db import init_db
from factory.logging import FactoryLogger
from factory.work_items import WorkItemOps


class WorkItemManagementApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(tempfile.mkstemp(prefix="factory_wi_mgmt_", suffix=".db")[1])
        init_db(self.path).close()
        self.prev_db = os.environ.get("FACTORY_DB")
        self.prev_api_key = os.environ.get("FACTORY_API_KEY")
        self.prev_api_server_db_path = api_server.DB_PATH
        self.prev_work_items_router_db_path = work_items_router.DB_PATH
        os.environ["FACTORY_DB"] = str(self.path)
        os.environ["FACTORY_API_KEY"] = "test-api-key"
        api_server.DB_PATH = self.path
        work_items_router.DB_PATH = self.path
        from fastapi.testclient import TestClient

        self.client = TestClient(app)
        self.headers = {"X-API-Key": "test-api-key"}

    def tearDown(self) -> None:
        if self.prev_db is None:
            os.environ.pop("FACTORY_DB", None)
        else:
            os.environ["FACTORY_DB"] = self.prev_db
        if self.prev_api_key is None:
            os.environ.pop("FACTORY_API_KEY", None)
        else:
            os.environ["FACTORY_API_KEY"] = self.prev_api_key
        api_server.DB_PATH = self.prev_api_server_db_path
        work_items_router.DB_PATH = self.prev_work_items_router_db_path
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def test_cancel_work_item(self) -> None:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        log = FactoryLogger(conn)
        ops = WorkItemOps(conn, log)
        vid = ops.create_vision("V", auto_commit=False)
        eid = ops.create_child(vid, "epic", "E", auto_commit=False)
        aid = ops.create_child(eid, "atom", "A", auto_commit=False)
        conn.execute("UPDATE work_items SET status = 'planned' WHERE id = ?", (vid,))
        conn.execute("UPDATE work_items SET status = 'planned' WHERE id = ?", (eid,))
        conn.execute(
            "UPDATE work_items SET status = 'ready_for_work' WHERE id = ?", (aid,)
        )
        conn.commit()
        conn.close()

        r = self.client.post(f"/api/work-items/{vid}/cancel", headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json().get("ok"))
        self.assertGreaterEqual(r.json().get("cancelled_count", 0), 3)

        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        for i in (vid, eid, aid):
            st = conn.execute("SELECT status FROM work_items WHERE id = ?", (i,)).fetchone()[0]
            self.assertEqual(st, "cancelled")
        conn.close()

        r2 = self.client.post(f"/api/work-items/{vid}/cancel", headers=self.headers)
        self.assertEqual(r2.status_code, 400)

    def test_archive_work_item(self) -> None:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        log = FactoryLogger(conn)
        ops = WorkItemOps(conn, log)
        vid = ops.create_vision("V2", auto_commit=False)
        eid = ops.create_child(vid, "epic", "E", auto_commit=False)
        aid = ops.create_child(eid, "atom", "A", auto_commit=False)
        for i in (vid, eid, aid):
            conn.execute("UPDATE work_items SET status = 'done' WHERE id = ?", (i,))
        conn.commit()
        conn.close()

        r = self.client.post(f"/api/work-items/{vid}/archive", headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json().get("ok"))
        self.assertGreaterEqual(r.json().get("archived_count", 0), 3)

        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        for i in (vid, eid, aid):
            st = conn.execute("SELECT status FROM work_items WHERE id = ?", (i,)).fetchone()[0]
            self.assertEqual(st, "archived")
        conn.close()

        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        log = FactoryLogger(conn)
        ops = WorkItemOps(conn, log)
        vid2 = ops.create_vision("V3", auto_commit=False)
        conn.execute("UPDATE work_items SET status = 'planned' WHERE id = ?", (vid2,))
        conn.commit()
        conn.close()
        r2 = self.client.post(f"/api/work-items/{vid2}/archive", headers=self.headers)
        self.assertEqual(r2.status_code, 400)

    def test_delete_work_item(self) -> None:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        log = FactoryLogger(conn)
        ops = WorkItemOps(conn, log)
        vid = ops.create_vision("V4", auto_commit=False)
        conn.commit()
        conn.close()

        r = self.client.delete(f"/api/work-items/{vid}", headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json().get("ok"))

        conn = sqlite3.connect(str(self.path))
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM work_items WHERE id = ?", (vid,)
        ).fetchone()
        self.assertEqual(int(row[0]), 0)
        conn.close()

        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        log = FactoryLogger(conn)
        ops = WorkItemOps(conn, log)
        vid = ops.create_vision("V5", auto_commit=False)
        aid = ops.create_child(vid, "atom", "A", auto_commit=False)
        conn.execute("UPDATE work_items SET status = 'in_progress' WHERE id = ?", (aid,))
        conn.commit()
        conn.close()

        r2 = self.client.delete(f"/api/work-items/{vid}", headers=self.headers)
        self.assertEqual(r2.status_code, 400)

    def test_edit_work_item(self) -> None:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        log = FactoryLogger(conn)
        ops = WorkItemOps(conn, log)
        vid = ops.create_vision("V6", auto_commit=False)
        conn.execute("UPDATE work_items SET status = 'planned' WHERE id = ?", (vid,))
        aid = ops.create_child(vid, "atom", "A", auto_commit=False)
        conn.execute("UPDATE work_items SET status = 'in_progress' WHERE id = ?", (aid,))
        conn.commit()
        conn.close()

        r = self.client.patch(
            f"/api/work-items/{vid}",
            json={"title": "NewTitle"},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["work_item"]["title"], "NewTitle")

        r2 = self.client.patch(
            f"/api/work-items/{aid}",
            json={"title": "X"},
            headers=self.headers,
        )
        self.assertEqual(r2.status_code, 400)


if __name__ == "__main__":
    unittest.main()
