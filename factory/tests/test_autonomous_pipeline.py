"""E2E: Vision → Epic → Story → 2 атома; tick + async workers → done + rollup (FACTORY_QWEN_DRY_RUN=1)."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from factory.composition import wire
from factory.db import gen_id, init_db
from factory.models import EventType


class AutonomousPipelineTests(unittest.TestCase):
    def test_vision_two_atoms_forge_review_judge_rollup(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_auto_pipe_", suffix=".db")[1])
        prev_dry = os.environ.get("FACTORY_QWEN_DRY_RUN")
        prev_async = os.environ.get("FACTORY_ORCHESTRATOR_ASYNC")
        try:
            os.environ["FACTORY_QWEN_DRY_RUN"] = "1"
            os.environ["FACTORY_ORCHESTRATOR_ASYNC"] = "1"
            os.environ["FACTORY_DB_PATH"] = str(path)

            conn = init_db(path)
            now = "2026-03-30T12:00:00.000000Z"
            vid = "vis_auto_pipe"
            eid = "epi_auto_pipe"
            sid = "sto_auto_pipe"
            a1, a2 = "atm_auto_p1", "atm_auto_p2"

            conn.execute(
                """
                INSERT INTO work_items (
                    id, parent_id, root_id, kind, title, description, status,
                    creator_role, owner_role, planning_depth, priority,
                    created_at, updated_at
                )
                VALUES (?, NULL, ?, 'vision', 'V', '', 'planned',
                        'planner', 'planner', 0, 100, ?, ?)
                """,
                (vid, vid, now, now),
            )
            conn.execute(
                """
                INSERT INTO work_items (
                    id, parent_id, root_id, kind, title, description, status,
                    creator_role, owner_role, planning_depth, priority,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, 'epic', 'E', '', 'planned',
                        'planner', 'planner', 1, 100, ?, ?)
                """,
                (eid, vid, vid, now, now),
            )
            conn.execute(
                """
                INSERT INTO work_items (
                    id, parent_id, root_id, kind, title, description, status,
                    creator_role, owner_role, planning_depth, priority,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, 'story', 'S', '', 'planned',
                        'planner', 'planner', 2, 100, ?, ?)
                """,
                (sid, eid, vid, now, now),
            )
            for aid, fpath in (
                (a1, "factory/__init__.py"),
                (a2, "factory/cli.py"),
            ):
                conn.execute(
                    """
                    INSERT INTO work_items (
                        id, parent_id, root_id, kind, title, description, status,
                        creator_role, owner_role, planning_depth, priority,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, 'atom', 'A', '', 'ready_for_work',
                            'planner', 'forge', 3, 100, ?, ?)
                    """,
                    (aid, sid, vid, now, now),
                )
                conn.execute(
                    """
                    INSERT INTO work_item_files (id, work_item_id, path, intent, description, required)
                    VALUES (?, ?, ?, 'modify', '', 1)
                    """,
                    (gen_id("wif"), aid, fpath),
                )
                conn.execute(
                    """
                    INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at)
                    VALUES (?, 'forge_inbox', 10, ?)
                    """,
                    (aid, now),
                )
            conn.commit()
            conn.close()

            f = wire(path)
            orch = f["orchestrator"]
            conn = f["conn"]

            from factory.orchestrator_core import wait_for_async_workers

            deadline = time.monotonic() + 120.0
            vision_done = False
            while time.monotonic() < deadline:
                orch.tick()
                wait_for_async_workers(timeout=15.0)
                row = conn.execute(
                    "SELECT status FROM work_items WHERE id = ?",
                    (vid,),
                ).fetchone()
                if row and row["status"] == "done":
                    vision_done = True
                    break
                time.sleep(0.05)

            self.assertTrue(vision_done, "Vision should reach done within timeout")

            for wid in (a1, a2):
                st = conn.execute(
                    "SELECT status FROM work_items WHERE id = ?",
                    (wid,),
                ).fetchone()["status"]
                self.assertEqual(st, "done", wid)

            self.assertEqual(
                conn.execute(
                    "SELECT status FROM work_items WHERE id = ?",
                    (sid,),
                ).fetchone()["status"],
                "done",
            )
            self.assertEqual(
                conn.execute(
                    "SELECT status FROM work_items WHERE id = ?",
                    (eid,),
                ).fetchone()["status"],
                "done",
            )

            ev = conn.execute(
                """
                SELECT COUNT(*) AS c FROM event_log
                WHERE event_type IN (?, ?, ?, ?, ?)
                """,
                (
                    EventType.FORGE_COMPLETED.value,
                    EventType.REVIEW_PASSED.value,
                    EventType.JUDGE_APPROVED.value,
                    EventType.ORCHESTRATOR_AUTO_FORGE_STARTED.value,
                    EventType.ORCHESTRATOR_AUTO_REVIEW_STARTED.value,
                ),
            ).fetchone()["c"]
            self.assertGreaterEqual(int(ev), 3, "event_log should contain pipeline milestones")

            f["conn"].close()
        finally:
            if prev_dry is None:
                os.environ.pop("FACTORY_QWEN_DRY_RUN", None)
            else:
                os.environ["FACTORY_QWEN_DRY_RUN"] = prev_dry
            if prev_async is None:
                os.environ.pop("FACTORY_ORCHESTRATOR_ASYNC", None)
            else:
                os.environ["FACTORY_ORCHESTRATOR_ASYNC"] = prev_async
            os.environ.pop("FACTORY_DB_PATH", None)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
