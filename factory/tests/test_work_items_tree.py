"""build_work_items_tree: иерархия и last_event для атомов."""

from __future__ import annotations

import json
import unittest

from factory.db import gen_id, init_db
from factory.models import EventType
from factory.work_items_tree import build_work_items_tree


class WorkItemsTreeTests(unittest.TestCase):
    def test_nested_and_atom_last_event(self) -> None:
        import tempfile
        from pathlib import Path

        path = Path(tempfile.mkstemp(prefix="wi_tree_", suffix=".db")[1])
        try:
            conn = init_db(path)
            t = "2026-03-30T12:00:00.000000"
            conn.execute(
                """
                INSERT INTO work_items (
                    id, parent_id, root_id, kind, title, description, status,
                    creator_role, owner_role, planning_depth, created_at, updated_at
                )
                VALUES ('vis1', NULL, 'vis1', 'vision', 'V', '', 'draft', 'c', 'c', 0, ?, ?)
                """,
                (t, t),
            )
            conn.execute(
                """
                INSERT INTO work_items (
                    id, parent_id, root_id, kind, title, description, status,
                    creator_role, owner_role, planning_depth, created_at, updated_at
                )
                VALUES ('atm1', 'vis1', 'vis1', 'atom', 'A', '', 'ready_for_work', 'c', 'forge', 1, ?, ?)
                """,
                (t, t),
            )
            conn.execute(
                """
                INSERT INTO event_log (
                    event_time, event_type, entity_type, entity_id, work_item_id,
                    severity, message, payload
                )
                VALUES (?, ?, 'work_item', 'atm1', 'atm1', 'info', 'x', ?)
                """,
                (t, EventType.TASK_CREATED.value, json.dumps({})),
            )
            conn.commit()

            tree = build_work_items_tree(conn)
            self.assertEqual(len(tree), 1)
            self.assertEqual(tree[0]["kind"], "vision")
            self.assertEqual(len(tree[0]["children"]), 1)
            atm = tree[0]["children"][0]
            self.assertEqual(atm["kind"], "atom")
            self.assertEqual(atm["last_event"], EventType.TASK_CREATED.value)
            self.assertEqual(atm["kind_short"], "ATM")
            conn.close()
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
