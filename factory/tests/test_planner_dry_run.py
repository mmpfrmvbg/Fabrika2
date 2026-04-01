"""Planner DRY_RUN: осмысленная декомпозиция + запись в SQLite."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from factory.agents.planner import decompose_with_planner
from factory.contracts.planner import PlannerInput
from factory.db import init_db
from factory.logging import FactoryLogger
from factory.work_items import WorkItemOps


class PlannerDryRunTests(unittest.TestCase):
    def test_dry_run_auth_like_description_creates_multiple_atoms(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_planner_dry_", suffix=".db")[1])
        prev_dry = os.environ.get("FACTORY_QWEN_DRY_RUN")
        try:
            os.environ["FACTORY_QWEN_DRY_RUN"] = "1"
            conn = init_db(path)
            logger = FactoryLogger(conn)
            ops = WorkItemOps(conn, logger)
            vid = ops.create_vision(
                "Auth system",
                "Сделать систему авторизации с JWT, регистрацией и сбросом пароля",
                auto_commit=False,
            )
            conn.commit()

            out = decompose_with_planner(
                conn=conn,
                logger=logger,
                inp=PlannerInput(
                    work_item_id=vid,
                    title="Auth system",
                    description="Сделать систему авторизации с JWT, регистрацией и сбросом пароля",
                    kind="vision",
                    current_depth=0,
                    max_depth=4,
                ),
            )
            self.assertGreaterEqual(len(out.items), 1)

            epics = conn.execute("SELECT COUNT(*) AS c FROM work_items WHERE root_id = ? AND kind='epic'", (vid,)).fetchone()["c"]
            stories = conn.execute("SELECT COUNT(*) AS c FROM work_items WHERE root_id = ? AND kind='story'", (vid,)).fetchone()["c"]
            atoms = conn.execute("SELECT COUNT(*) AS c FROM work_items WHERE root_id = ? AND kind='atom'", (vid,)).fetchone()["c"]
            self.assertGreaterEqual(int(epics), 1)
            self.assertGreaterEqual(int(stories), 2)
            self.assertGreaterEqual(int(atoms), 2)

            ready = conn.execute(
                "SELECT COUNT(*) AS c FROM work_items WHERE root_id = ? AND kind='atom' AND status='ready_for_work'",
                (vid,),
            ).fetchone()["c"]
            self.assertEqual(int(ready), int(atoms))

            q = conn.execute(
                "SELECT COUNT(*) AS c FROM work_item_queue WHERE work_item_id IN (SELECT id FROM work_items WHERE root_id=? AND kind='atom') AND queue_name='forge_inbox'",
                (vid,),
            ).fetchone()["c"]
            self.assertEqual(int(q), int(atoms))

            wif = conn.execute(
                "SELECT COUNT(*) AS c FROM work_item_files WHERE work_item_id IN (SELECT id FROM work_items WHERE root_id=? AND kind='atom')",
                (vid,),
            ).fetchone()["c"]
            self.assertGreaterEqual(int(wif), int(atoms))

            # acceptance criteria в описании атомов
            ac = conn.execute(
                "SELECT COUNT(*) AS c FROM work_items WHERE root_id=? AND kind='atom' AND description LIKE 'Acceptance criteria:%'",
                (vid,),
            ).fetchone()["c"]
            self.assertGreaterEqual(int(ac), 1)

            conn.close()
        finally:
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

