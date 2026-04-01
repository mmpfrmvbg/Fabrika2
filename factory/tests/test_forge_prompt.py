"""Юнит: ``build_forge_prompt`` без Qwen."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from factory.composition import wire
from factory.forge_prompt import FORGE_SYSTEM_PREAMBLE, build_forge_prompt
from factory.models import WorkItemStatus


class ForgePromptTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.setdefault("FACTORY_API_KEY_1", "ut-forge-prompt")
        self._fd, self._path = tempfile.mkstemp(prefix="factory_ut_fp_", suffix=".db")
        os.close(self._fd)
        self.db_path = Path(self._path)

    def tearDown(self) -> None:
        try:
            self.db_path.unlink(missing_ok=True)
        except OSError:
            pass

    def test_build_includes_preamble_task_and_declared_file(self) -> None:
        f = wire(self.db_path)
        conn = f["conn"]
        wi_id = "ato_ut_fp"
        conn.execute(
            """
            INSERT INTO work_items (
                id, parent_id, root_id, kind, title, description,
                status, creator_role, owner_role, planning_depth
            )
            VALUES (?, NULL, ?, 'atom', 'T', 'D',
                    ?, 'planner', 'forge', 0)
            """,
            (wi_id, wi_id, WorkItemStatus.READY_FOR_WORK.value),
        )
        conn.execute(
            """
            INSERT INTO work_item_files (id, work_item_id, path, intent, description, required)
            VALUES ('wif1', ?, 'x.txt', 'modify', 'touch', 1)
            """,
            (wi_id,),
        )
        conn.commit()

        repo = Path(tempfile.mkdtemp(prefix="repo_fp_"))
        try:
            (repo / "x.txt").write_text("line\n", encoding="utf-8")
            text = build_forge_prompt(conn, wi_id, repo_root=repo)
        finally:
            import shutil

            shutil.rmtree(repo, ignore_errors=True)

        self.assertIn(FORGE_SYSTEM_PREAMBLE[:40], text)
        self.assertIn("x.txt", text)
        self.assertIn("modify", text)
        self.assertIn("line", text)
        self.assertIn("work_item_id:", text)
        self.assertIn("title: T", text)
        self.assertIn("Mandatory output behavior", text)
        self.assertIn("directly editing the declared files", text)

    def test_build_includes_previous_feedback_block_from_decisions_and_comments(self) -> None:
        f = wire(self.db_path)
        conn = f["conn"]
        wi_id = "ato_ut_fp_fb"
        conn.execute(
            """
            INSERT INTO work_items (
                id, parent_id, root_id, kind, title, description,
                status, creator_role, owner_role, planning_depth
            )
            VALUES (?, NULL, ?, 'atom', 'T2', 'D2',
                    ?, 'planner', 'forge', 0)
            """,
            (wi_id, wi_id, WorkItemStatus.READY_FOR_WORK.value),
        )
        conn.execute(
            """
            INSERT INTO work_item_files (id, work_item_id, path, intent, description, required)
            VALUES ('wif2', ?, 'y.txt', 'create', 'add', 1)
            """,
            (wi_id,),
        )
        conn.execute(
            """
            INSERT INTO decisions
                (id, work_item_id, run_id, decision_role, verdict, reason_code, explanation, suggested_fix)
            VALUES
                ('dec1', ?, NULL, 'judge', 'rejected', 'scope', 'missing date output', 'Print today as YYYY-MM-DD')
            """,
            (wi_id,),
        )
        conn.execute(
            """
            INSERT INTO comments
                (id, work_item_id, author_role, comment_type, body)
            VALUES
                ('c1', ?, 'judge', 'instruction', 'Add date output in YYYY-MM-DD')
            """,
            (wi_id,),
        )
        conn.commit()

        repo = Path(tempfile.mkdtemp(prefix="repo_fp_fb_"))
        try:
            text = build_forge_prompt(conn, wi_id, repo_root=repo)
        finally:
            import shutil

            shutil.rmtree(repo, ignore_errors=True)

        self.assertIn("## Previous feedback (MUST address)", text)
        self.assertIn("role=judge verdict=rejected", text)
        self.assertIn("suggested_fix: Print today as YYYY-MM-DD", text)
        self.assertIn("Add date output in YYYY-MM-DD", text)


if __name__ == "__main__":
    unittest.main()
