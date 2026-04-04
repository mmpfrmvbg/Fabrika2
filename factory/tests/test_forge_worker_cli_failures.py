"""
Forge-worker + мок ``run_qwen_cli``: ненулевой exit, таймаут, max_tries (без account_exhausted).

Проверяются ``runs.status``, ``EventType.RUN_FAILED_CLI_ERROR`` / ``RUN_FAILED_ACCOUNT_ROTATION_LIMIT``,
отсутствие ``run.failed.account_exhausted`` где не нужно.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.agents.forge import run_forge_queued_runs
from factory.composition import wire
from factory.db import gen_id
from factory.models import EventType, Role, RunStatus, RunType, WorkItemStatus
from factory.qwen_cli_runner import ForgeResult

_PATCH = "factory.forge_worker.run_qwen_cli"


def _seed_atom_forge_queued(conn, wi_id: str, run_id: str) -> None:
    acc = conn.execute("SELECT id FROM api_accounts LIMIT 1").fetchone()
    if not acc:
        raise RuntimeError("api_accounts пуст")
    account_id = acc["id"]
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description,
            status, creator_role, owner_role, planning_depth
        )
        VALUES (?, NULL, ?, 'atom', 'ut', 't',
                ?, 'planner', 'forge', 0)
        """,
        (wi_id, wi_id, WorkItemStatus.IN_PROGRESS.value),
    )
    conn.execute(
        """
        INSERT INTO runs (id, work_item_id, agent_id, account_id, role, run_type, status)
        VALUES (?, ?, ?, ?, ?, ?, 'queued')
        """,
        (
            run_id,
            wi_id,
            f"agent_{Role.FORGE.value}",
            account_id,
            Role.FORGE.value,
            RunType.IMPLEMENT.value,
        ),
    )
    conn.commit()


class ForgeWorkerCliFailuresTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.setdefault("FACTORY_API_KEY_1", "unit-test-forge-failures")
        self._fd, self._path = tempfile.mkstemp(prefix="factory_ut_forge_", suffix=".db")
        os.close(self._fd)
        self.db_path = Path(self._path)

    def tearDown(self) -> None:
        try:
            self.db_path.unlink(missing_ok=True)
        except OSError:
            pass

    def test_nonzero_exit_generic_run_failed_not_account_exhausted(self) -> None:
        f = wire(self.db_path)
        conn = f["conn"]
        orch = f["orchestrator"]
        wi_id = "wi_ut_nz"
        run_id = "run_ut_nz"
        _seed_atom_forge_queued(conn, wi_id, run_id)

        fr = ForgeResult(
            ok=False,
            exit_code=99,
            stderr="build failed",
            error_message="build failed",
        )

        with patch(_PATCH, return_value=fr):
            run_forge_queued_runs(orch)

        row = conn.execute(
            "SELECT status FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        self.assertEqual(row["status"], RunStatus.FAILED.value)

        bad = conn.execute(
            """
            SELECT COUNT(*) AS c FROM event_log
            WHERE run_id = ? AND event_type = ?
            """,
            (run_id, EventType.RUN_FAILED_ACCOUNT_EXHAUSTED.value),
        ).fetchone()["c"]
        self.assertEqual(bad, 0)

        cli_ev = conn.execute(
            """
            SELECT COUNT(*) AS c FROM event_log
            WHERE run_id = ? AND event_type = ?
            """,
            (run_id, EventType.RUN_FAILED_CLI_ERROR.value),
        ).fetchone()["c"]
        self.assertGreaterEqual(cli_ev, 1)

        st = conn.execute(
            "SELECT status FROM work_items WHERE id = ?",
            (wi_id,),
        ).fetchone()["status"]
        self.assertEqual(st, WorkItemStatus.READY_FOR_WORK.value)

    def test_timeout_summary_generic_run_failed(self) -> None:
        f = wire(self.db_path)
        conn = f["conn"]
        orch = f["orchestrator"]
        wi_id = "wi_ut_to"
        run_id = "run_ut_to"
        _seed_atom_forge_queued(conn, wi_id, run_id)

        fr = ForgeResult(
            ok=False,
            exit_code=-1,
            error_message="Qwen CLI timeout (600s)",
        )

        with patch(_PATCH, return_value=fr):
            run_forge_queued_runs(orch)

        self.assertEqual(
            conn.execute(
                "SELECT status FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()["status"],
            RunStatus.FAILED.value,
        )
        ex = conn.execute(
            """
            SELECT COUNT(*) AS c FROM event_log
            WHERE run_id = ? AND event_type = ?
            """,
            (run_id, EventType.RUN_FAILED_ACCOUNT_EXHAUSTED.value),
        ).fetchone()["c"]
        self.assertEqual(ex, 0)
        cli_ev = conn.execute(
            """
            SELECT COUNT(*) AS c FROM event_log
            WHERE run_id = ? AND event_type = ?
            """,
            (run_id, EventType.RUN_FAILED_CLI_ERROR.value),
        ).fetchone()["c"]
        self.assertGreaterEqual(cli_ev, 1)

    def test_max_tries_reached_not_account_exhausted_event(self) -> None:
        f = wire(self.db_path)
        conn = f["conn"]
        orch = f["orchestrator"]
        wi_id = "wi_ut_mx"
        run_id = "run_ut_mx"
        _seed_atom_forge_queued(conn, wi_id, run_id)

        fr = ForgeResult(
            ok=False,
            max_tries_reached=True,
            error_message="Исчерпан лимит итераций (4) без успешного вызова",
            accounts_tried=["a", "b", "c", "d"],
        )

        with patch(_PATCH, return_value=fr):
            run_forge_queued_runs(orch)

        self.assertEqual(
            conn.execute(
                "SELECT status FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()["status"],
            RunStatus.FAILED.value,
        )
        self.assertEqual(
            conn.execute(
                """
                SELECT COUNT(*) AS c FROM event_log
                WHERE run_id = ? AND event_type = ?
                """,
                (run_id, EventType.RUN_FAILED_ACCOUNT_EXHAUSTED.value),
            ).fetchone()["c"],
            0,
        )
        rot = conn.execute(
            """
            SELECT COUNT(*) AS c FROM event_log
            WHERE run_id = ? AND event_type = ?
            """,
            (run_id, EventType.RUN_FAILED_ACCOUNT_ROTATION_LIMIT.value),
        ).fetchone()["c"]
        self.assertGreaterEqual(rot, 1)

    def test_wet_forge_ok_but_no_file_changes_emits_forge_no_artifact(self) -> None:
        """Wet + ForgeResult.ok без diff по modify-файлам → run.failed.forge_no_artifact + forge_failed."""
        prev_dry = os.environ.get("FACTORY_QWEN_DRY_RUN")
        os.environ["FACTORY_QWEN_DRY_RUN"] = "0"
        try:
            f = wire(self.db_path)
            conn = f["conn"]
            orch = f["orchestrator"]
            wi_id = "wi_ut_noart"
            run_id = "run_ut_noart"
            _seed_atom_forge_queued(conn, wi_id, run_id)
            conn.execute(
                """
                INSERT INTO work_item_files (id, work_item_id, path, intent, description, required)
                VALUES (?, ?, 'factory/models.py', 'modify', 'ut', 1)
                """,
                (gen_id("wif"), wi_id),
            )
            conn.commit()

            fr = ForgeResult(ok=True, exit_code=0, stdout="ok", stderr="")

            with patch(_PATCH, return_value=fr):
                run_forge_queued_runs(orch)

            row = conn.execute(
                "SELECT status FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            self.assertEqual(row["status"], RunStatus.FAILED.value)

            na = conn.execute(
                """
                SELECT COUNT(*) AS c FROM event_log
                WHERE run_id = ? AND event_type = ?
                """,
                (run_id, EventType.RUN_FAILED_FORGE_NO_ARTIFACT.value),
            ).fetchone()["c"]
            self.assertGreaterEqual(na, 1)

            st = conn.execute(
                "SELECT status FROM work_items WHERE id = ?",
                (wi_id,),
            ).fetchone()["status"]
            self.assertEqual(st, WorkItemStatus.READY_FOR_WORK.value)
        finally:
            if prev_dry is None:
                os.environ.pop("FACTORY_QWEN_DRY_RUN", None)
            else:
                os.environ["FACTORY_QWEN_DRY_RUN"] = prev_dry

    def test_idempotency_cache_hit_skips_second_llm_call(self) -> None:
        f = wire(self.db_path)
        conn = f["conn"]
        orch = f["orchestrator"]
        wi_id = "wi_ut_cache"
        run_1 = "run_ut_cache_1"
        run_2 = "run_ut_cache_2"
        _seed_atom_forge_queued(conn, wi_id, run_1)

        fr = ForgeResult(ok=True, exit_code=0, stdout="ok", stderr="")
        with patch(_PATCH, return_value=fr):
            run_forge_queued_runs(orch)

        row_1 = conn.execute("SELECT status, input_hash FROM runs WHERE id = ?", (run_1,)).fetchone()
        self.assertEqual(row_1["status"], RunStatus.COMPLETED.value)
        self.assertTrue((row_1["input_hash"] or "").strip())

        acc = conn.execute("SELECT id FROM api_accounts LIMIT 1").fetchone()
        conn.execute("UPDATE work_items SET status = ? WHERE id = ?", (WorkItemStatus.IN_PROGRESS.value, wi_id))
        conn.execute(
            """
            INSERT INTO runs (id, work_item_id, agent_id, account_id, role, run_type, status)
            VALUES (?, ?, ?, ?, ?, ?, 'queued')
            """,
            (
                run_2,
                wi_id,
                f"agent_{Role.FORGE.value}",
                acc["id"],
                Role.FORGE.value,
                RunType.IMPLEMENT.value,
            ),
        )
        conn.commit()

        with patch(_PATCH, side_effect=AssertionError("LLM call must be skipped on cache hit")):
            run_forge_queued_runs(orch)

        row_2 = conn.execute("SELECT status, input_hash FROM runs WHERE id = ?", (run_2,)).fetchone()
        self.assertEqual(row_2["status"], RunStatus.COMPLETED.value)
        self.assertEqual(row_1["input_hash"], row_2["input_hash"])

        steps_2 = conn.execute("SELECT COUNT(*) AS c FROM run_steps WHERE run_id = ?", (run_2,)).fetchone()["c"]
        self.assertEqual(int(steps_2), 0)


if __name__ == "__main__":
    unittest.main()
