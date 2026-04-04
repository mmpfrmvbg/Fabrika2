from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from factory.api_server import app
from factory.db import gen_id, init_db


class ApiRunsEffectiveTests(unittest.TestCase):
    def test_effective_endpoint_and_run_serialization(self) -> None:
        db = Path(tempfile.mkstemp(prefix="factory_runs_", suffix=".db")[1])
        prev_db = os.environ.get("FACTORY_DB")
        try:
            conn = init_db(db)
            now = "2026-04-04T12:00:00.000000Z"
            conn.execute(
                """
                INSERT INTO work_items (
                    id, parent_id, root_id, kind, title, description, status,
                    creator_role, owner_role, planning_depth, priority, created_at, updated_at
                )
                VALUES ('atom_runs', NULL, 'atom_runs', 'atom', 'A', '', 'in_progress', 'c', 'forge', 1, 1, ?, ?)
                """,
                (now, now),
            )
            source_run_id = "run_src_1"
            cache_hit_run_id = "run_cache_1"
            conn.execute(
                """
                INSERT INTO runs (
                    id, work_item_id, agent_id, role, run_type, status, started_at, finished_at, source_run_id, dry_run
                )
                VALUES (?, 'atom_runs', 'agent_forge', 'forge', 'implement', 'done', ?, ?, NULL, 0)
                """,
                (source_run_id, now, now),
            )
            conn.execute(
                """
                INSERT INTO runs (
                    id, work_item_id, agent_id, role, run_type, status, started_at, finished_at, source_run_id, dry_run
                )
                VALUES (?, 'atom_runs', 'agent_forge', 'forge', 'implement', 'done', ?, ?, ?, 1)
                """,
                (cache_hit_run_id, now, now, source_run_id),
            )
            conn.execute(
                """
                INSERT INTO run_steps (
                    id, run_id, step_no, step_kind, status, summary, payload, created_at
                )
                VALUES (?, ?, 1, 'apply', 'done', 'applied changes', '{}', ?)
                """,
                (gen_id("rs"), source_run_id, now),
            )
            conn.execute(
                """
                INSERT INTO file_changes (
                    id, work_item_id, run_id, path, change_type, diff_summary, lines_added, lines_removed, created_at
                )
                VALUES (?, 'atom_runs', ?, 'factory/demo.py', 'modify', 'changed', 2, 1, ?)
                """,
                (gen_id("fc"), source_run_id, now),
            )
            conn.commit()
            conn.close()

            os.environ["FACTORY_DB"] = str(db)
            client = TestClient(app)

            runs_list = client.get("/api/runs")
            self.assertEqual(runs_list.status_code, 200)
            by_id = {x["id"]: x for x in runs_list.json()["items"]}
            self.assertIn("source_run_id", by_id[cache_hit_run_id])
            self.assertIn("dry_run", by_id[cache_hit_run_id])
            self.assertEqual(by_id[cache_hit_run_id]["source_run_id"], source_run_id)
            self.assertTrue(by_id[cache_hit_run_id]["dry_run"])

            run_detail = client.get(f"/api/runs/{cache_hit_run_id}")
            self.assertEqual(run_detail.status_code, 200)
            detail = run_detail.json()
            self.assertEqual(detail["run"]["effective_run_id"], source_run_id)
            self.assertEqual(detail["run"]["source_run_id"], source_run_id)
            self.assertTrue(detail["run"]["dry_run"])
            self.assertEqual(len(detail["run_steps"]), 1)
            self.assertEqual(len(detail["file_changes"]), 1)
            self.assertEqual(detail["file_changes"][0]["path"], "factory/demo.py")

            effective = client.get(f"/api/runs/{cache_hit_run_id}/effective")
            self.assertEqual(effective.status_code, 200)
            self.assertEqual(effective.json(), {"effective_run_id": source_run_id})
        finally:
            if prev_db is None:
                os.environ.pop("FACTORY_DB", None)
            else:
                os.environ["FACTORY_DB"] = prev_db
            try:
                db.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
