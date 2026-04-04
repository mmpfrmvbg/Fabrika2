from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.db import init_db
from factory.logging import FactoryLogger
from factory.queue_ops import claim_forge_inbox_atom
from factory.worker import recover_stuck_running_work_items


class StuckRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(tempfile.mkstemp(prefix="factory_stuck_recovery_", suffix=".db")[1])
        self.conn = init_db(self.path)
        self.logger = FactoryLogger(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def test_schema_v10_adds_last_heartbeat_at_column(self) -> None:
        cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(work_items)").fetchall()
        }
        self.assertIn("last_heartbeat_at", cols)

    def test_recover_stuck_running_item_with_null_heartbeat(self) -> None:
        self.conn.execute(
            """
            INSERT INTO work_items (
                id, parent_id, root_id, kind, title, description, status,
                previous_status, creator_role, owner_role, planning_depth, priority
            )
            VALUES ('wi_stuck_null', NULL, 'wi_stuck_null', 'atom', 'A', '', 'running',
                    'ready_for_work', 'planner', 'forge', 0, 100)
            """
        )
        self.conn.commit()

        recovered = recover_stuck_running_work_items(
            self.conn, self.logger, worker_id="worker-test"
        )
        self.conn.commit()
        self.assertEqual(recovered, 1)

        row = self.conn.execute(
            "SELECT status, previous_status, last_heartbeat_at FROM work_items WHERE id = 'wi_stuck_null'"
        ).fetchone()
        self.assertEqual(row["status"], "ready_for_work")
        self.assertEqual(row["previous_status"], "running")
        self.assertIsNone(row["last_heartbeat_at"])

        ev = self.conn.execute(
            """
            SELECT event_type FROM event_log
            WHERE work_item_id = 'wi_stuck_null'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        self.assertIsNotNone(ev)
        self.assertEqual(ev["event_type"], "work_item.recovered")

    def test_does_not_recover_recent_running_item(self) -> None:
        self.conn.execute(
            """
            INSERT INTO work_items (
                id, parent_id, root_id, kind, title, description, status,
                previous_status, creator_role, owner_role, planning_depth, priority, last_heartbeat_at
            )
            VALUES (
                'wi_recent_running', NULL, 'wi_recent_running', 'atom', 'A2', '', 'running',
                'ready_for_work', 'planner', 'forge', 0, 100, strftime('%Y-%m-%dT%H:%M:%f','now')
            )
            """
        )
        self.conn.commit()

        recovered = recover_stuck_running_work_items(
            self.conn, self.logger, worker_id="worker-test"
        )
        self.conn.commit()
        self.assertEqual(recovered, 0)

        row = self.conn.execute(
            "SELECT status FROM work_items WHERE id = 'wi_recent_running'"
        ).fetchone()
        self.assertEqual(row["status"], "running")

    def test_recovery_releases_queue_lease_and_item_can_be_reclaimed(self) -> None:
        self.conn.execute(
            """
            INSERT INTO work_items (
                id, parent_id, root_id, kind, title, description, status,
                previous_status, creator_role, owner_role, planning_depth, priority
            )
            VALUES ('wi_crash', NULL, 'wi_crash', 'atom', 'Crash atom', '', 'running',
                    'ready_for_work', 'planner', 'forge', 0, 100)
            """
        )
        self.conn.execute(
            """
            INSERT INTO work_item_queue (
                work_item_id, queue_name, lease_owner, lease_until, attempts, max_attempts
            )
            VALUES (
                'wi_crash', 'forge_inbox', 'worker-1',
                strftime('%Y-%m-%dT%H:%M:%f','now','+10 minutes'), 0, 3
            )
            """
        )
        self.conn.commit()

        recovered = recover_stuck_running_work_items(
            self.conn, self.logger, worker_id="worker-test"
        )
        self.conn.commit()
        self.assertEqual(recovered, 1)

        qrow = self.conn.execute(
            "SELECT 1 FROM work_item_queue WHERE work_item_id = 'wi_crash'"
        ).fetchone()
        self.assertIsNone(qrow)

        self.conn.execute(
            """
            INSERT INTO work_item_queue (work_item_id, queue_name, attempts, max_attempts)
            VALUES ('wi_crash', 'forge_inbox', 0, 3)
            """
        )
        claimed = claim_forge_inbox_atom(self.conn, "worker-recover")
        self.assertEqual(claimed, "wi_crash")


if __name__ == "__main__":
    unittest.main()
