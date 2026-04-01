"""
Юнит: forge-worker при exhausted_accounts от run_qwen_cli → failed run + событие + forge_failed.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.agents.forge import run_forge_queued_runs
from factory.composition import wire
from factory.models import EventType, Role, RunStatus, RunType, WorkItemStatus
from factory.qwen_cli_runner import ForgeResult

# Патчим вызов в forge_worker.execute_forge_run
_PATCH_TARGET = "factory.forge_worker.run_qwen_cli"


def _seed_atom_forge_queued(conn, wi_id: str, run_id: str) -> None:
    acc = conn.execute("SELECT id FROM api_accounts LIMIT 1").fetchone()
    if not acc:
        raise RuntimeError("api_accounts пуст — задайте FACTORY_API_KEY_1 для теста")
    account_id = acc["id"]
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description,
            status, creator_role, owner_role, planning_depth
        )
        VALUES (?, NULL, ?, 'atom', 'unit forge', 'test',
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


class ForgeAccountExhaustedTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.setdefault("FACTORY_API_KEY_1", "unit-test-key-forge-exhausted")
        self._fd, self._path = tempfile.mkstemp(prefix="factory_ut_", suffix=".db")
        os.close(self._fd)
        self.db_path = Path(self._path)

    def tearDown(self) -> None:
        try:
            self.db_path.unlink(missing_ok=True)
        except OSError:
            pass

    def test_exhausted_accounts_failed_run_event_and_fsm(self) -> None:
        f = wire(self.db_path)
        conn = f["conn"]
        orch = f["orchestrator"]
        wi_id = "wi_ut_exhausted"
        run_id = "run_ut_exhausted"

        _seed_atom_forge_queued(conn, wi_id, run_id)

        fr = ForgeResult(
            ok=False,
            exhausted_accounts=True,
            error_message="all slots busy (mock)",
            accounts_tried=["acc_a", "acc_b"],
        )

        with patch(_PATCH_TARGET, return_value=fr) as m:
            run_forge_queued_runs(orch)
            m.assert_called_once()

        row = conn.execute(
            "SELECT status, error_summary FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        self.assertEqual(row["status"], RunStatus.FAILED.value)
        self.assertEqual(row["error_summary"], fr.error_message)

        ev = conn.execute(
            """
            SELECT COUNT(*) AS c FROM event_log
            WHERE run_id = ? AND event_type = ?
            """,
            (run_id, EventType.RUN_FAILED_ACCOUNT_EXHAUSTED.value),
        ).fetchone()["c"]
        self.assertGreaterEqual(ev, 1, "ожидался run.failed.account_exhausted в event_log")

        fsm_ev = conn.execute(
            """
            SELECT COUNT(*) AS c FROM event_log
            WHERE work_item_id = ? AND event_type = ?
            """,
            (wi_id, EventType.FORGE_FAILED.value),
        ).fetchone()["c"]
        self.assertGreaterEqual(fsm_ev, 1, "ожидался forge.failed по work_item после forge_failed")

        st = conn.execute(
            "SELECT status FROM work_items WHERE id = ?",
            (wi_id,),
        ).fetchone()["status"]
        self.assertEqual(
            st,
            WorkItemStatus.READY_FOR_WORK.value,
            "forge_failed + guard_can_retry → ready_for_work",
        )


if __name__ == "__main__":
    unittest.main()
