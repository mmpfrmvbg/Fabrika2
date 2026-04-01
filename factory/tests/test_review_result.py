"""ReviewResult: парсинг, FSM, БД, журнал."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from factory.composition import wire
from factory.contracts.review import (
    ReviewResultValidationError,
    parse_review_result,
    validate_review_fsm_alignment,
    validate_subject_run_alignment,
)
from factory.e2e_golden import _seed_judge_forge_chain, _seed_review_only
from factory.models import EventType, WorkItemStatus


class ParseReviewResultTests(unittest.TestCase):
    def test_parse_approved_ok(self) -> None:
        raw = json.dumps(
            {
                "item": "atom:wi_x",
                "run_id": "run_f1",
                "verdict": "approved",
                "checked_artifacts": ["file_changes"],
                "all_passed": True,
                "issues": [],
                "context_refs": [],
                "next_event": "review_passed",
            },
            ensure_ascii=False,
        )
        r = parse_review_result(raw)
        self.assertEqual(r.verdict, "approved")
        validate_review_fsm_alignment(result=r)

    def test_run_id_coerces_int(self) -> None:
        raw = json.dumps(
            {
                "item": "atom:wi_x",
                "run_id": 99,
                "verdict": "approved",
                "checked_artifacts": ["a"],
                "all_passed": True,
                "issues": [],
                "context_refs": [],
                "next_event": "review_passed",
            },
            ensure_ascii=False,
        )
        r = parse_review_result(raw)
        self.assertEqual(r.run_id, "99")

    def test_approved_with_high_issue_fails(self) -> None:
        raw = json.dumps(
            {
                "item": "atom:wi_x",
                "run_id": "r1",
                "verdict": "approved",
                "checked_artifacts": ["a"],
                "all_passed": True,
                "issues": [
                    {"code": "x", "severity": "high", "message": "bad"},
                ],
                "context_refs": [],
                "next_event": "review_passed",
            },
            ensure_ascii=False,
        )
        with self.assertRaises(ReviewResultValidationError):
            parse_review_result(raw)

    def test_rejected_requires_issues(self) -> None:
        raw = json.dumps(
            {
                "item": "atom:wi_x",
                "run_id": "r1",
                "verdict": "rejected",
                "checked_artifacts": ["a"],
                "all_passed": False,
                "issues": [],
                "context_refs": [],
                "next_event": "review_failed",
            },
            ensure_ascii=False,
        )
        with self.assertRaises(ReviewResultValidationError):
            parse_review_result(raw)

    def test_wrong_next_event(self) -> None:
        raw = json.dumps(
            {
                "item": "atom:wi_x",
                "run_id": "r1",
                "verdict": "approved",
                "checked_artifacts": ["a"],
                "all_passed": True,
                "issues": [],
                "context_refs": [],
                "next_event": "review_failed",
            },
            ensure_ascii=False,
        )
        r = parse_review_result(raw)
        with self.assertRaises(ReviewResultValidationError):
            validate_review_fsm_alignment(result=r)

    def test_subject_run_mismatch(self) -> None:
        raw = json.dumps(
            {
                "item": "atom:wi_x",
                "run_id": "wrong",
                "verdict": "approved",
                "checked_artifacts": ["a"],
                "all_passed": True,
                "issues": [],
                "context_refs": [],
                "next_event": "review_passed",
            },
            ensure_ascii=False,
        )
        r = parse_review_result(raw)
        with self.assertRaises(ReviewResultValidationError):
            validate_subject_run_alignment(
                result=r,
                latest_implement_run_id="expected_run",
            )


class ReviewIntegrationTests(unittest.TestCase):
    def test_golden_review_persists_result_and_done(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_rr_", suffix=".db")[1])
        f = None
        _prev_dry = os.environ.get("FACTORY_QWEN_DRY_RUN")
        os.environ["FACTORY_QWEN_DRY_RUN"] = "1"
        try:
            f = wire(path)
            conn, orch = f["conn"], f["orchestrator"]
            wi_id = "e2e_atm_review"
            _seed_review_only(conn, wi_id)
            conn.commit()
            orch.tick()
            st = conn.execute(
                "SELECT status FROM work_items WHERE id = ?",
                (wi_id,),
            ).fetchone()["status"]
            self.assertEqual(st, WorkItemStatus.DONE.value)
            rc = conn.execute(
                "SELECT COUNT(*) AS c FROM review_results WHERE work_item_id = ?",
                (wi_id,),
            ).fetchone()["c"]
            self.assertEqual(rc, 1)
            ev = conn.execute(
                """
                SELECT COUNT(*) AS c FROM event_log
                WHERE work_item_id = ? AND event_type = ?
                """,
                (wi_id, EventType.REVIEW_RESULT.value),
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

    def test_invalid_json_no_fsm_done(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_rr_bad_", suffix=".db")[1])
        f = None
        _prev = os.environ.pop("FACTORY_REVIEW_FORCE_INVALID_JSON", None)
        _prev_dry = os.environ.get("FACTORY_QWEN_DRY_RUN")
        os.environ["FACTORY_QWEN_DRY_RUN"] = "1"
        os.environ["FACTORY_REVIEW_FORCE_INVALID_JSON"] = "1"
        try:
            f = wire(path)
            conn, orch = f["conn"], f["orchestrator"]
            wi_id = "e2e_atm_review_bad"
            _seed_review_only(conn, wi_id)
            conn.commit()
            orch.tick()
            st = conn.execute(
                "SELECT status FROM work_items WHERE id = ?",
                (wi_id,),
            ).fetchone()["status"]
            self.assertEqual(st, WorkItemStatus.IN_REVIEW.value)
            rc = conn.execute(
                "SELECT COUNT(*) AS c FROM review_results WHERE work_item_id = ?",
                (wi_id,),
            ).fetchone()["c"]
            self.assertEqual(rc, 0)
            bad = conn.execute(
                """
                SELECT COUNT(*) AS c FROM event_log
                WHERE work_item_id = ? AND event_type = ?
                """,
                (wi_id, EventType.REVIEW_INVALID_OUTPUT.value),
            ).fetchone()["c"]
            self.assertGreaterEqual(bad, 1)
        finally:
            if _prev is None:
                os.environ.pop("FACTORY_REVIEW_FORCE_INVALID_JSON", None)
            else:
                os.environ["FACTORY_REVIEW_FORCE_INVALID_JSON"] = _prev
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

    def test_force_reject_review_failed_loop(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_rr_rej_", suffix=".db")[1])
        f = None
        _prev_rej = os.environ.pop("FACTORY_REVIEW_FORCE_REJECT", None)
        _prev_dry = os.environ.get("FACTORY_QWEN_DRY_RUN")
        os.environ["FACTORY_REVIEW_FORCE_REJECT"] = "1"
        os.environ["FACTORY_QWEN_DRY_RUN"] = "1"
        try:
            f = wire(path)
            conn, orch = f["conn"], f["orchestrator"]
            wi_id = "rr_reject_wi"
            _seed_judge_forge_chain(conn, wi_id)
            conn.commit()
            orch.tick()  # judge → ready_for_work + forge_inbox
            orch.tick()  # forge (dry) + review (reject) + st_16 + в том же тике judge снова одобряет
            st = conn.execute(
                "SELECT status FROM work_items WHERE id = ?",
                (wi_id,),
            ).fetchone()["status"]
            self.assertEqual(st, WorkItemStatus.READY_FOR_WORK.value)
            rc = conn.execute(
                "SELECT COUNT(*) AS c FROM review_results WHERE work_item_id = ? AND verdict = ?",
                (wi_id, "rejected"),
            ).fetchone()["c"]
            self.assertEqual(rc, 1)
            ev = conn.execute(
                """
                SELECT COUNT(*) AS c FROM event_log
                WHERE work_item_id = ? AND event_type = ?
                """,
                (wi_id, EventType.REVIEW_REJECTED.value),
            ).fetchone()["c"]
            self.assertGreaterEqual(ev, 1)
        finally:
            if _prev_rej is None:
                os.environ.pop("FACTORY_REVIEW_FORCE_REJECT", None)
            else:
                os.environ["FACTORY_REVIEW_FORCE_REJECT"] = _prev_rej
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
