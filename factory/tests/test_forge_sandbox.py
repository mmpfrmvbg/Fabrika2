"""Юнит: песочница + ``capture_changes`` + dry placeholder."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from factory.composition import wire
from factory.forge_sandbox import (
    apply_dry_run_placeholder,
    capture_changes,
    cleanup_sandbox,
    prepare_sandbox,
    safe_path_under_workspace,
)
from factory.models import Role, RunStatus, RunType, WorkItemStatus


class ForgeSandboxTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.setdefault("FACTORY_API_KEY_1", "ut-forge-sandbox")
        self._fd, self._path = tempfile.mkstemp(prefix="factory_ut_fs_", suffix=".db")
        os.close(self._fd)
        self.db_path = Path(self._path)

    def tearDown(self) -> None:
        try:
            self.db_path.unlink(missing_ok=True)
        except OSError:
            pass

    def test_prepare_apply_dry_capture(self) -> None:
        f = wire(self.db_path)
        conn = f["conn"]
        wi_id = "ato_ut_fs"
        run_id = "run_ut_fs"
        acc = conn.execute("SELECT id FROM api_accounts LIMIT 1").fetchone()["id"]

        conn.execute(
            """
            INSERT INTO work_items (
                id, parent_id, root_id, kind, title, description,
                status, creator_role, owner_role, planning_depth
            )
            VALUES (?, NULL, ?, 'atom', 'sandbox', 'test',
                    ?, 'planner', 'forge', 0)
            """,
            (wi_id, wi_id, WorkItemStatus.IN_PROGRESS.value),
        )
        conn.execute(
            """
            INSERT INTO work_item_files (id, work_item_id, path, intent, description, required)
            VALUES ('wif1', ?, 'a.py', 'modify', 'm', 1)
            """,
            (wi_id,),
        )
        conn.execute(
            """
            INSERT INTO runs (id, work_item_id, agent_id, account_id, role, run_type, status)
            VALUES (?, ?, ?, ?, ?, ?, 'running')
            """,
            (
                run_id,
                wi_id,
                f"agent_{Role.FORGE.value}",
                acc,
                Role.FORGE.value,
                RunType.IMPLEMENT.value,
            ),
        )
        conn.commit()

        repo = Path(tempfile.mkdtemp(prefix="repo_fs_"))
        ctx = None
        try:
            (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
            ctx = prepare_sandbox(conn, wi_id, run_id, repo_root=repo)
            self.assertTrue(ctx.root.is_dir())
            self.assertTrue((ctx.root / "a.py").is_file())

            apply_dry_run_placeholder(ctx, conn, wi_id)
            changes = capture_changes(ctx)
            self.assertGreaterEqual(len(changes), 1)
            self.assertEqual(changes[0].path, "a.py")
            self.assertEqual(changes[0].change_type, "modify")
            self.assertIsNotNone(changes[0].new_hash)
        finally:
            cleanup_sandbox(ctx)
            import shutil

            shutil.rmtree(repo, ignore_errors=True)

    def test_path_traversal_blocked(self) -> None:
        base = Path(tempfile.mkdtemp(prefix="factory_ut_ws_")).resolve()
        try:
            with self.assertRaises(ValueError) as cm:
                safe_path_under_workspace(base, "../outside.txt")
            self.assertIn("traversal", str(cm.exception).lower())
        finally:
            import shutil

            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
