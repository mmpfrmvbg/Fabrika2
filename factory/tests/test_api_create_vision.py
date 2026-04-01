"""POST /api/visions: создание Vision + planner decompose (DRY_RUN)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from factory.api_server import create_vision
from factory.db import init_db
from factory.models import EventType


class CreateVisionApiTests(unittest.TestCase):
    def test_create_vision_persists_tree_and_event(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_api_vis_", suffix=".db")[1])
        prev = os.environ.get("FACTORY_DB")
        prev_dry = os.environ.get("FACTORY_QWEN_DRY_RUN")
        try:
            init_db(path).close()
            os.environ["FACTORY_DB"] = str(path)
            os.environ["FACTORY_QWEN_DRY_RUN"] = "1"

            out = create_vision({"title": "T1", "description": "D1"})
            self.assertTrue(out.get("ok"))
            vid = out.get("id")
            self.assertTrue(isinstance(vid, str) and vid)
            self.assertIn("tree_stats", out)
            self.assertIn("tree", out)
            self.assertIsInstance(out["tree"], list)
            self.assertEqual(len(out["tree"]), 1)
            self.assertEqual(out["tree"][0].get("id"), vid)
            self.assertEqual(out["tree"][0].get("kind"), "vision")
            self.assertGreater(len(out["tree"][0].get("children") or []), 0)
            ts = out["tree_stats"]
            self.assertGreaterEqual(int(ts.get("epics", 0)), 1)
            self.assertGreaterEqual(int(ts.get("atoms", 0)), 2)

            conn = sqlite3.connect(str(path))
            conn.row_factory = sqlite3.Row
            v = conn.execute("SELECT kind, root_id, parent_id FROM work_items WHERE id = ?", (vid,)).fetchone()
            self.assertIsNotNone(v)
            self.assertEqual(v["kind"], "vision")
            self.assertEqual(v["root_id"], vid)
            self.assertIsNone(v["parent_id"])

            # есть хотя бы один atom ready_for_work + в очереди
            atom_id = conn.execute(
                "SELECT id FROM work_items WHERE root_id = ? AND kind = 'atom' ORDER BY created_at ASC LIMIT 1",
                (vid,),
            ).fetchone()["id"]
            a = conn.execute("SELECT status, owner_role, parent_id FROM work_items WHERE id = ?", (atom_id,)).fetchone()
            self.assertEqual(a["status"], "ready_for_work")
            self.assertEqual(a["owner_role"], "forge")

            q = conn.execute("SELECT queue_name FROM work_item_queue WHERE work_item_id = ?", (atom_id,)).fetchone()
            self.assertIsNotNone(q)
            self.assertEqual(q["queue_name"], "forge_inbox")

            wif = conn.execute("SELECT COUNT(*) AS c FROM work_item_files WHERE work_item_id = ?", (atom_id,)).fetchone()["c"]
            self.assertGreaterEqual(int(wif), 1)

            ev = conn.execute(
                "SELECT COUNT(*) AS c FROM event_log WHERE work_item_id = ? AND event_type = ?",
                (vid, EventType.VISION_CREATED.value),
            ).fetchone()["c"]
            self.assertGreaterEqual(int(ev), 1)
            conn.close()
        finally:
            if prev is None:
                os.environ.pop("FACTORY_DB", None)
            else:
                os.environ["FACTORY_DB"] = prev
            if prev_dry is None:
                os.environ.pop("FACTORY_QWEN_DRY_RUN", None)
            else:
                os.environ["FACTORY_QWEN_DRY_RUN"] = prev_dry
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()

