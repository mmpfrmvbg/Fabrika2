"""Судья: окно event_log в шаге prompt и флаг used_event_log в JudgeVerdict."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from factory.composition import wire
from factory.contracts.judge import parse_judge_verdict
from factory.e2e_golden import _seed_judge_forge_chain
from factory.models import EventType, Role


class JudgeJournalContextTests(unittest.TestCase):
    def test_prompt_includes_journal_block_and_verdict_used_event_log(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_judge_journal_", suffix=".db")[1])
        f = None
        try:
            f = wire(path)
            conn, orch = f["conn"], f["orchestrator"]
            wi_id = "jv_journal_ctx"
            _seed_judge_forge_chain(conn, wi_id)
            conn.execute(
                """
                INSERT INTO event_log (
                    event_time, event_type, entity_type, entity_id, work_item_id, run_id,
                    actor_role, severity, message, payload
                )
                VALUES (?, ?, 'work_item', ?, ?, NULL, ?, 'info', ?, ?)
                """,
                (
                    "2026-03-30T09:00:00.000000",
                    EventType.TASK_STATUS_CHANGED.value,
                    wi_id,
                    wi_id,
                    Role.ORCHESTRATOR.value,
                    "transition: draft → planned",
                    json.dumps({"message": "orchestrator note"}),
                ),
            )
            conn.execute(
                """
                INSERT INTO event_log (
                    event_time, event_type, entity_type, entity_id, work_item_id, run_id,
                    actor_role, severity, message, payload
                )
                VALUES (?, ?, 'work_item', ?, ?, NULL, ?, 'info', ?, ?)
                """,
                (
                    "2026-03-30T09:01:00.000000",
                    EventType.FORGE_STARTED.value,
                    wi_id,
                    wi_id,
                    Role.FORGE.value,
                    "prior forge audit line",
                    json.dumps({"dry_run": True}),
                ),
            )
            conn.commit()

            orch.tick()

            row = conn.execute(
                """
                SELECT rs.payload
                FROM run_steps rs
                JOIN runs r ON rs.run_id = r.id
                WHERE r.work_item_id = ? AND r.run_type = 'judge' AND rs.step_no = 1
                ORDER BY r.started_at DESC
                LIMIT 1
                """,
                (wi_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            pl = json.loads(row["payload"])
            self.assertIn("journal_context_block", pl)
            self.assertIn("### Recent factory events for this work item", pl["journal_context_block"])
            self.assertIn("[orchestrator]", pl["journal_context_block"])
            self.assertIn("[forge]", pl["journal_context_block"])

            jv_row = conn.execute(
                "SELECT payload_json FROM judge_verdicts WHERE work_item_id = ?",
                (wi_id,),
            ).fetchone()
            self.assertIsNotNone(jv_row)
            verdict_dump = json.loads(jv_row["payload_json"])
            self.assertTrue(verdict_dump.get("used_event_log"))
            v = parse_judge_verdict(json.dumps(verdict_dump))
            self.assertTrue(v.used_event_log)
        finally:
            if f is not None:
                f["conn"].close()
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
