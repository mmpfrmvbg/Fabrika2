"""
Wet-path unit/integration: reviewer/judge call qwen_cli_runner when FACTORY_QWEN_DRY_RUN=0.

We patch run_qwen_cli to avoid requiring real Qwen CLI in tests.
"""

from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.composition import wire
from factory.e2e_golden import _seed_judge_forge_chain, _seed_review_only
from factory.models import WorkItemStatus
from factory.qwen_cli_runner import ForgeResult


class ReviewerWetLLMTests(unittest.TestCase):
    def test_reviewer_uses_effective_source_run_artifacts_on_cache_hit(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_rr_wet_cache_", suffix=".db")[1])
        f = None
        prev = os.environ.get("FACTORY_QWEN_DRY_RUN")
        os.environ["FACTORY_QWEN_DRY_RUN"] = "0"
        try:
            f = wire(path)
            conn, orch = f["conn"], f["orchestrator"]
            wi_id = "wet_rr_cache_hit"
            source_run_id = "run_source_impl"
            cached_run_id = "run_cached_impl"

            conn.execute(
                """
                INSERT INTO work_items
                    (id, root_id, kind, title, description, status, creator_role, owner_role, planning_depth)
                VALUES (?, ?, 'atom', 'cache-hit review', 'seed', ?, 'planner', 'reviewer', 0)
                """,
                (wi_id, wi_id, WorkItemStatus.IN_REVIEW.value),
            )
            conn.execute(
                "INSERT INTO work_item_queue (work_item_id, queue_name) VALUES (?, 'review_inbox')",
                (wi_id,),
            )
            conn.execute(
                """
                INSERT INTO runs (id, work_item_id, agent_id, account_id, role, run_type, status, started_at, finished_at)
                VALUES (?, ?, 'agent_forge', (SELECT id FROM api_accounts LIMIT 1), 'forge', 'implement', 'completed', '2026-04-04T10:00:00', '2026-04-04T10:00:10')
                """,
                (source_run_id, wi_id),
            )
            conn.execute(
                """
                INSERT INTO runs (
                    id, work_item_id, agent_id, account_id, role, run_type, status,
                    source_run_id, started_at, finished_at
                )
                VALUES (?, ?, 'agent_forge', (SELECT id FROM api_accounts LIMIT 1), 'forge', 'implement', 'completed',
                        ?, '2026-04-04T10:01:00', '2026-04-04T10:01:05')
                """,
                (cached_run_id, wi_id, source_run_id),
            )
            conn.execute(
                """
                INSERT INTO file_changes (id, work_item_id, run_id, path, change_type, diff_summary)
                VALUES ('fc_src', ?, ?, 'factory/hello_qwen.py', 'modify', '@@ -1 +1 @@\\n-print(1)\\n+print(2)')
                """,
                (wi_id, source_run_id),
            )
            conn.execute(
                """
                INSERT INTO run_steps (id, run_id, step_no, step_kind, status, summary, payload)
                VALUES ('rs_src', ?, 1, 'tool_result', 'completed', 'qwen_cli_runner', '{}')
                """,
                (source_run_id,),
            )
            conn.commit()

            captured_prompt: dict[str, str] = {}

            def _fake_reviewer_cli(**kwargs):
                captured_prompt["text"] = kwargs.get("full_prompt", "")
                return ForgeResult(ok=True, stdout="APPROVED", stderr="", exit_code=0)

            with patch("factory.agents.reviewer.run_qwen_cli", side_effect=_fake_reviewer_cli):
                orch.tick()

            self.assertIn("factory/hello_qwen.py", captured_prompt.get("text", ""))
            self.assertIn("print(2)", captured_prompt.get("text", ""))

            rr = conn.execute(
                "SELECT subject_run_id, payload_json FROM review_results WHERE work_item_id = ? ORDER BY created_at DESC LIMIT 1",
                (wi_id,),
            ).fetchone()
            self.assertIsNotNone(rr)
            self.assertEqual(rr["subject_run_id"], cached_run_id)
            payload = json.loads(rr["payload_json"] or "{}")
            self.assertEqual(payload.get("run_id"), cached_run_id)

            ev = conn.execute(
                """
                SELECT payload FROM event_log
                WHERE work_item_id = ? AND event_type = 'review.result'
                ORDER BY id DESC LIMIT 1
                """,
                (wi_id,),
            ).fetchone()
            ev_payload = json.loads(ev["payload"] or "{}")
            self.assertEqual(ev_payload.get("effective_subject_run_id"), source_run_id)
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
