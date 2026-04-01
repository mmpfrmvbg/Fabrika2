"""Planner: workspace tree в промпте + DRY_RUN находит calculator/calc.py без явного пути."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from factory.agents.planner import build_planner_prompt, decompose_with_planner
from factory.contracts.planner import PlannerInput
from factory.db import init_db
from factory.logging import FactoryLogger
from factory.work_items import WorkItemOps


class PlannerWorkspaceStructureTests(unittest.TestCase):
    def test_build_planner_prompt_includes_workspace_structure(self) -> None:
        prev = os.environ.get("FACTORY_WORKSPACE_ROOT")
        try:
            root = Path(__file__).resolve().parents[2]
            os.environ["FACTORY_WORKSPACE_ROOT"] = str(root)
            text = build_planner_prompt(
                PlannerInput(
                    work_item_id="wi_test",
                    title="t",
                    description="d",
                    kind="vision",
                )
            )
            self.assertIn("Current workspace structure", text)
            self.assertIn("calculator/", text)
            self.assertIn("calc.py", text)
        finally:
            if prev is None:
                os.environ.pop("FACTORY_WORKSPACE_ROOT", None)
            else:
                os.environ["FACTORY_WORKSPACE_ROOT"] = prev

    def test_dry_run_calculator_vision_includes_calc_py_modify(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_planner_ws_", suffix=".db")[1])
        prev_dry = os.environ.get("FACTORY_QWEN_DRY_RUN")
        prev_root = os.environ.get("FACTORY_WORKSPACE_ROOT")
        try:
            os.environ["FACTORY_QWEN_DRY_RUN"] = "1"
            os.environ["FACTORY_WORKSPACE_ROOT"] = str(Path(__file__).resolve().parents[2])
            conn = init_db(path)
            logger = FactoryLogger(conn)
            ops = WorkItemOps(conn, logger)
            vid = ops.create_vision(
                "Калькулятор",
                "добавь метод power в калькулятор",
                auto_commit=False,
            )
            conn.commit()

            decompose_with_planner(
                conn=conn,
                logger=logger,
                inp=PlannerInput(
                    work_item_id=vid,
                    title="Калькулятор",
                    description="добавь метод power в калькулятор",
                    kind="vision",
                    current_depth=0,
                    max_depth=4,
                ),
            )
            row = conn.execute(
                """
                SELECT path, intent FROM work_item_files
                WHERE work_item_id IN (
                    SELECT id FROM work_items WHERE root_id = ? AND kind = 'atom'
                )
                AND path = 'calculator/calc.py'
                """,
                (vid,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["path"], "calculator/calc.py")
            self.assertEqual(row["intent"], "modify")
            conn.close()
        finally:
            if prev_dry is None:
                os.environ.pop("FACTORY_QWEN_DRY_RUN", None)
            else:
                os.environ["FACTORY_QWEN_DRY_RUN"] = prev_dry
            if prev_root is None:
                os.environ.pop("FACTORY_WORKSPACE_ROOT", None)
            else:
                os.environ["FACTORY_WORKSPACE_ROOT"] = prev_root
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
