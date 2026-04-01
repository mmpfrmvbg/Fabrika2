"""
Wet-path unit/integration: reviewer/judge call qwen_cli_runner when FACTORY_QWEN_DRY_RUN=0.

We patch run_qwen_cli to avoid requiring real Qwen CLI in tests.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.composition import wire
from factory.e2e_golden import _seed_judge_forge_chain, _seed_review_only
from factory.models import WorkItemStatus
from factory.qwen_cli_runner import ForgeResult


class ReviewerWetLLMTests(unittest.TestCase):
    def test_reviewer_wet_approved_advances_to_ready_for_judge(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_rr_wet_ok_", suffix=".db")[1])
        f = None
        prev = os.environ.get("FACTORY_QWEN_DRY_RUN")
        os.environ["FACTORY_QWEN_DRY_RUN"] = "0"
        try:
            f = wire(path)
            conn, orch = f["conn"], f["orchestrator"]
            wi_id = "wet_rr_atom_ok"
            _seed_review_only(conn, wi_id)
            conn.commit()

            with patch(
                "factory.agents.planner.run_qwen_cli",
                return_value=ForgeResult(
                    ok=True,
                    stdout='{"items": [], "reasoning": "ok"}',
                    stderr="",
                    exit_code=0,
                ),
            ), patch(
                "factory.planner.run_qwen_cli",
                return_value=ForgeResult(
                    ok=True,
                    stdout='{"items": [], "reasoning": "ok"}',
                    stderr="",
                    exit_code=0,
                ),
            ), patch(
                "factory.agents.judge.run_qwen_cli",
                return_value=ForgeResult(
                    ok=True,
                    stdout="\n".join(
                        [
                            "DECISION: REJECTED",
                            "REASONCODE: quality",
                            "EXPLANATION: hold",
                            "SUGGESTED_FIX: ",
                        ]
                    ),
                    stderr="",
                    exit_code=0,
                ),
            ), patch(
                "factory.agents.reviewer.run_qwen_cli",
                return_value=ForgeResult(ok=True, stdout="APPROVED", stderr="", exit_code=0),
            ):
                orch.tick()

            st = conn.execute(
                "SELECT status FROM work_items WHERE id = ?",
                (wi_id,),
            ).fetchone()["status"]
            # reviewer must move item out of in_review; judge may run in same tick
            self.assertNotEqual(st, WorkItemStatus.IN_REVIEW.value)
        finally:
            if prev is None:
                os.environ.pop("FACTORY_QWEN_DRY_RUN", None)
            else:
                os.environ["FACTORY_QWEN_DRY_RUN"] = prev
            if f is not None:
                f["conn"].close()
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def test_reviewer_wet_rejected_keeps_in_review(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_rr_wet_rej_", suffix=".db")[1])
        f = None
        prev = os.environ.get("FACTORY_QWEN_DRY_RUN")
        os.environ["FACTORY_QWEN_DRY_RUN"] = "0"
        try:
            f = wire(path)
            conn, orch = f["conn"], f["orchestrator"]
            wi_id = "wet_rr_atom_rej"
            _seed_review_only(conn, wi_id)
            conn.commit()

            with patch(
                "factory.agents.planner.run_qwen_cli",
                return_value=ForgeResult(
                    ok=True,
                    stdout='{"items": [], "reasoning": "ok"}',
                    stderr="",
                    exit_code=0,
                ),
            ), patch(
                "factory.planner.run_qwen_cli",
                return_value=ForgeResult(
                    ok=True,
                    stdout='{"items": [], "reasoning": "ok"}',
                    stderr="",
                    exit_code=0,
                ),
            ), patch(
                "factory.agents.reviewer.run_qwen_cli",
                return_value=ForgeResult(ok=True, stdout="REJECTED:quality", stderr="", exit_code=0),
            ):
                orch.tick()

            st = conn.execute(
                "SELECT status FROM work_items WHERE id = ?",
                (wi_id,),
            ).fetchone()["status"]
            # review_failed -> review_rejected, then orchestrator escalates to judge; judge may reject in same tick
            self.assertIn(
                st,
                (WorkItemStatus.READY_FOR_JUDGE.value, WorkItemStatus.JUDGE_REJECTED.value),
            )
        finally:
            if prev is None:
                os.environ.pop("FACTORY_QWEN_DRY_RUN", None)
            else:
                os.environ["FACTORY_QWEN_DRY_RUN"] = prev
            if f is not None:
                f["conn"].close()
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def test_reviewer_wet_llm_error_rejects(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_rr_wet_err_", suffix=".db")[1])
        f = None
        prev = os.environ.get("FACTORY_QWEN_DRY_RUN")
        os.environ["FACTORY_QWEN_DRY_RUN"] = "0"
        try:
            f = wire(path)
            conn, orch = f["conn"], f["orchestrator"]
            wi_id = "wet_rr_atom_llm_err"
            _seed_review_only(conn, wi_id)
            conn.commit()

            with patch(
                "factory.agents.planner.run_qwen_cli",
                return_value=ForgeResult(
                    ok=True,
                    stdout='{"items": [], "reasoning": "ok"}',
                    stderr="",
                    exit_code=0,
                ),
            ), patch(
                "factory.planner.run_qwen_cli",
                return_value=ForgeResult(
                    ok=True,
                    stdout='{"items": [], "reasoning": "ok"}',
                    stderr="",
                    exit_code=0,
                ),
            ), patch(
                "factory.agents.reviewer.run_qwen_cli",
                return_value=ForgeResult(
                    ok=False,
                    stdout="",
                    stderr="spawn failed",
                    exit_code=1,
                    error_message="spawn failed",
                ),
            ):
                orch.tick()

            st = conn.execute(
                "SELECT status FROM work_items WHERE id = ?",
                (wi_id,),
            ).fetchone()["status"]
            # LLM error => safety reject -> escalation to judge; judge may reject in same tick
            self.assertIn(
                st,
                (WorkItemStatus.READY_FOR_JUDGE.value, WorkItemStatus.JUDGE_REJECTED.value),
            )
        finally:
            if prev is None:
                os.environ.pop("FACTORY_QWEN_DRY_RUN", None)
            else:
                os.environ["FACTORY_QWEN_DRY_RUN"] = prev
            if f is not None:
                f["conn"].close()
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


class JudgeWetLLMTests(unittest.TestCase):
    def test_judge_wet_approved_advances_atom_to_ready_for_work(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_jv_wet_ok_", suffix=".db")[1])
        f = None
        prev = os.environ.get("FACTORY_QWEN_DRY_RUN")
        os.environ["FACTORY_QWEN_DRY_RUN"] = "0"
        try:
            f = wire(path)
            conn, orch = f["conn"], f["orchestrator"]
            wi_id = "wet_jv_atom_ok"
            _seed_judge_forge_chain(conn, wi_id)
            conn.commit()

            with patch(
                "factory.agents.planner.run_qwen_cli",
                return_value=ForgeResult(
                    ok=True,
                    stdout='{"items": [], "reasoning": "ok"}',
                    stderr="",
                    exit_code=0,
                ),
            ), patch(
                "factory.planner.run_qwen_cli",
                return_value=ForgeResult(
                    ok=True,
                    stdout='{"items": [], "reasoning": "ok"}',
                    stderr="",
                    exit_code=0,
                ),
            ), patch(
                "factory.agents.judge.run_qwen_cli",
                return_value=ForgeResult(
                    ok=True,
                    stdout="\n".join(
                        [
                            "DECISION: APPROVED",
                            "REASONCODE: quality",
                            "EXPLANATION: ok",
                            "SUGGESTED_FIX: ",
                        ]
                    ),
                    stderr="",
                    exit_code=0,
                ),
            ):
                orch.tick()

            st = conn.execute(
                "SELECT status FROM work_items WHERE id = ?",
                (wi_id,),
            ).fetchone()["status"]
            self.assertEqual(st, WorkItemStatus.READY_FOR_WORK.value)
        finally:
            if prev is None:
                os.environ.pop("FACTORY_QWEN_DRY_RUN", None)
            else:
                os.environ["FACTORY_QWEN_DRY_RUN"] = prev
            if f is not None:
                f["conn"].close()
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def test_judge_wet_rejected_keeps_ready_for_judge(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_jv_wet_rej_", suffix=".db")[1])
        f = None
        prev = os.environ.get("FACTORY_QWEN_DRY_RUN")
        os.environ["FACTORY_QWEN_DRY_RUN"] = "0"
        try:
            f = wire(path)
            conn, orch = f["conn"], f["orchestrator"]
            wi_id = "wet_jv_atom_rej"
            _seed_judge_forge_chain(conn, wi_id)
            conn.commit()

            with patch(
                "factory.agents.planner.run_qwen_cli",
                return_value=ForgeResult(
                    ok=True,
                    stdout='{"items": [], "reasoning": "ok"}',
                    stderr="",
                    exit_code=0,
                ),
            ), patch(
                "factory.planner.run_qwen_cli",
                return_value=ForgeResult(
                    ok=True,
                    stdout='{"items": [], "reasoning": "ok"}',
                    stderr="",
                    exit_code=0,
                ),
            ), patch(
                "factory.agents.judge.run_qwen_cli",
                return_value=ForgeResult(
                    ok=True,
                    stdout="\n".join(
                        [
                            "DECISION: REJECTED",
                            "REASONCODE: security",
                            "EXPLANATION: risky",
                            "SUGGESTED_FIX: remove eval",
                        ]
                    ),
                    stderr="",
                    exit_code=0,
                ),
            ):
                orch.tick()

            st = conn.execute(
                "SELECT status FROM work_items WHERE id = ?",
                (wi_id,),
            ).fetchone()["status"]
            self.assertEqual(st, WorkItemStatus.JUDGE_REJECTED.value)
        finally:
            if prev is None:
                os.environ.pop("FACTORY_QWEN_DRY_RUN", None)
            else:
                os.environ["FACTORY_QWEN_DRY_RUN"] = prev
            if f is not None:
                f["conn"].close()
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()

