"""JudgeVerdict: парсинг, FSM-стык, запись в БД и журнал."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from factory.composition import wire
from factory.contracts.judge import (
    JudgeVerdictValidationError,
    parse_judge_verdict,
    validate_verdict_fsm_alignment,
)
from factory.e2e_golden import _seed_judge_forge_chain
from factory.models import EventType, WorkItemStatus


class ParseJudgeVerdictTests(unittest.TestCase):
    def test_parse_approved_ok(self) -> None:
        raw = json.dumps(
            {
                "item": "atom:wi_x",
                "verdict": "approved",
                "checked_guards": ["g1"],
                "all_passed": True,
                "context_refs": [],
                "next_event": "judge_approved",
            },
            ensure_ascii=False,
        )
        v = parse_judge_verdict(raw)
        self.assertEqual(v.verdict, "approved")
        self.assertEqual(v.next_event, "judge_approved")

    def test_parse_invalid_json(self) -> None:
        with self.assertRaises(JudgeVerdictValidationError):
            parse_judge_verdict("not-json")

    def test_parse_rejected_requires_fields(self) -> None:
        raw = json.dumps(
            {
                "item": "story:s1",
                "verdict": "rejected",
                "checked_guards": ["a"],
                "all_passed": False,
                "context_refs": [],
                "next_event": "judge_rejected",
            },
            ensure_ascii=False,
        )
        with self.assertRaises(JudgeVerdictValidationError):
            parse_judge_verdict(raw)

    def test_fsm_alignment_rejected_ok(self) -> None:
        raw = json.dumps(
            {
                "item": "atom:wi_x",
                "verdict": "rejected",
                "checked_guards": ["g1"],
                "all_passed": False,
                "context_refs": [],
                "next_event": "judge_rejected",
                "failed_guards": ["g1"],
                "rejection_reason_code": "too_broad",
                "suggested_action": "split",
            },
            ensure_ascii=False,
        )
        v = parse_judge_verdict(raw)
        validate_verdict_fsm_alignment(work_item_kind="atom", verdict=v)


class JudgeE2EIntegrationTests(unittest.TestCase):
    def test_tick_persists_judge_verdict_and_advances_fsm(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_jv_", suffix=".db")[1])
        f = None
        _prev_dry = os.environ.get("FACTORY_QWEN_DRY_RUN")
        os.environ["FACTORY_QWEN_DRY_RUN"] = "1"
        try:
            f = wire(path)
            conn, orch = f["conn"], f["orchestrator"]
            wi_id = "jv_atom_ok"
            _seed_judge_forge_chain(conn, wi_id)
            conn.commit()

            orch.tick()

            st = conn.execute(
                "SELECT status FROM work_items WHERE id = ?",
                (wi_id,),
            ).fetchone()["status"]
            self.assertEqual(st, WorkItemStatus.READY_FOR_WORK.value)

            jv = conn.execute(
                "SELECT COUNT(*) AS c FROM judge_verdicts WHERE work_item_id = ?",
                (wi_id,),
            ).fetchone()["c"]
            self.assertEqual(jv, 1)

            ev = conn.execute(
                """
                SELECT COUNT(*) AS c FROM event_log
                WHERE work_item_id = ? AND event_type = ?
                """,
                (wi_id, EventType.JUDGE_VERDICT.value),
            ).fetchone()["c"]
            self.assertGreaterEqual(ev, 1)
        finally:
            if _prev_dry is None:
                os.environ.pop("FACTORY_QWEN_DRY_RUN", None)
            else:
                os.environ["FACTORY_QWEN_DRY_RUN"] = _prev_dry
            if f is not None:
                f["conn"].close()
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def test_invalid_json_does_not_apply_transition(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_jv_bad_", suffix=".db")[1])
        f = None
        _prev = os.environ.pop("FACTORY_JUDGE_FORCE_INVALID_JSON", None)
        _prev_dry = os.environ.get("FACTORY_QWEN_DRY_RUN")
        os.environ["FACTORY_QWEN_DRY_RUN"] = "1"
        os.environ["FACTORY_JUDGE_FORCE_INVALID_JSON"] = "1"
        try:
            f = wire(path)
            conn, orch = f["conn"], f["orchestrator"]
            wi_id = "jv_atom_bad"
            _seed_judge_forge_chain(conn, wi_id)
            conn.commit()

            orch.tick()

            st = conn.execute(
                "SELECT status FROM work_items WHERE id = ?",
                (wi_id,),
            ).fetchone()["status"]
            self.assertEqual(st, WorkItemStatus.READY_FOR_JUDGE.value)

            jv = conn.execute(
                "SELECT COUNT(*) AS c FROM judge_verdicts WHERE work_item_id = ?",
                (wi_id,),
            ).fetchone()["c"]
            self.assertEqual(jv, 0)

            bad_ev = conn.execute(
                """
                SELECT COUNT(*) AS c FROM event_log
                WHERE work_item_id = ? AND event_type = ?
                """,
                (wi_id, EventType.JUDGE_INVALID_OUTPUT.value),
            ).fetchone()["c"]
            self.assertGreaterEqual(bad_ev, 1)

            run_row = conn.execute(
                """
                SELECT status, error_summary FROM runs
                WHERE work_item_id = ? AND role = 'judge'
                ORDER BY started_at DESC LIMIT 1
                """,
                (wi_id,),
            ).fetchone()
            self.assertIsNotNone(run_row)
            self.assertEqual(run_row["status"], "failed")
            self.assertIn("judge_invalid_output", run_row["error_summary"] or "")
        finally:
            if _prev is None:
                os.environ.pop("FACTORY_JUDGE_FORCE_INVALID_JSON", None)
            else:
                os.environ["FACTORY_JUDGE_FORCE_INVALID_JSON"] = _prev
            if _prev_dry is None:
                os.environ.pop("FACTORY_QWEN_DRY_RUN", None)
            else:
                os.environ["FACTORY_QWEN_DRY_RUN"] = _prev_dry
            if f is not None:
                f["conn"].close()
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
