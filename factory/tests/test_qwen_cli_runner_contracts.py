"""
Контракты run_qwen_cli: ненулевой exit, таймаут, rate limit / max_tries / exhausted.

Патчится ``subprocess.run``; ``FACTORY_QWEN_DRY_RUN=0``.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import patch

import factory.qwen_cli_runner as qwen_cli_runner
from factory.qwen_cli_runner import _build_extra_argv, looks_rate_limited

from factory.composition import wire
from factory.models import Role, RunType
from factory.tests.qwen_cli_test_env import reload_config_with_account_keys, reload_single_test_account

WI_STUB = "wi_qwen_ut"
RUN_STUB = "run_qwen_ut"


class QwenCliRunnerDefaultArgvTest(unittest.TestCase):
    """Дефолтные флаги для неинтерактивного forge (yolo + max-session-turns)."""

    def tearDown(self) -> None:
        for k in (
            "FACTORY_QWEN_EXTRA_ARGS",
            "FACTORY_QWEN_MAX_SESSION_TURNS",
            "FACTORY_QWEN_CHANNEL",
        ):
            os.environ.pop(k, None)

    def test_build_extra_argv_adds_yolo_and_turns(self) -> None:
        os.environ.pop("FACTORY_QWEN_EXTRA_ARGS", None)
        os.environ.pop("FACTORY_QWEN_MAX_SESSION_TURNS", None)
        os.environ.pop("FACTORY_QWEN_CHANNEL", None)
        extra = _build_extra_argv()
        self.assertIn("--approval-mode", extra)
        self.assertIn("yolo", extra)
        self.assertIn("--max-session-turns", extra)
        ix = extra.index("--max-session-turns")
        self.assertEqual(extra[ix + 1], "25")

    def test_build_extra_argv_respects_explicit_approval_mode(self) -> None:
        os.environ["FACTORY_QWEN_EXTRA_ARGS"] = "--approval-mode default"
        extra = _build_extra_argv()
        self.assertIn("--approval-mode", extra)
        self.assertIn("default", extra)
        self.assertNotIn("yolo", extra)


def _stub_wi_and_run(conn) -> None:
    """Минимальные строки для FK event_log (work_item_id, run_id)."""
    acc = conn.execute("SELECT id FROM api_accounts ORDER BY priority LIMIT 1").fetchone()
    aid = acc["id"]
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description,
            status, creator_role, owner_role, planning_depth
        )
        VALUES (?, NULL, ?, 'atom', 'ut', '', 'in_progress', 'planner', 'forge', 0)
        """,
        (WI_STUB, WI_STUB),
    )
    conn.execute(
        """
        INSERT INTO runs (id, work_item_id, agent_id, account_id, role, run_type, status)
        VALUES (?, ?, ?, ?, ?, ?, 'running')
        """,
        (
            RUN_STUB,
            WI_STUB,
            f"agent_{Role.FORGE.value}",
            aid,
            Role.FORGE.value,
            RunType.IMPLEMENT.value,
        ),
    )
    conn.commit()


class QwenCliRunnerNonzeroExitTest(unittest.TestCase):
    def setUp(self) -> None:
        reload_config_with_account_keys(1)
        self._fd, self._raw = tempfile.mkstemp(prefix="factory_ut_qwen_", suffix=".db")
        os.close(self._fd)
        self.db_path = Path(self._raw)

    def tearDown(self) -> None:
        reload_single_test_account()
        try:
            self.db_path.unlink(missing_ok=True)
        except OSError:
            pass

    def test_nonzero_exit_no_rotation_second_account(self) -> None:
        """Ненулевой exit без маркеров rate limit — один вызов subprocess, один account_id в списке."""
        os.environ["FACTORY_QWEN_DRY_RUN"] = "0"
        os.environ["FACTORY_QWEN_MAX_ACCOUNT_TRIES"] = "12"
        f = wire(self.db_path)
        _stub_wi_and_run(f["conn"])
        cp = CompletedProcess(
            args=[],
            returncode=7,
            stdout=b"",
            stderr=b"build failed: no matching target",
        )

        def _fake_run(*_a, **_k):
            return cp

        with patch("factory.qwen_cli_runner.subprocess.run", side_effect=_fake_run):
            fr = qwen_cli_runner.run_qwen_cli(
                conn=f["conn"],
                account_manager=f["accounts"],
                logger=f["logger"],
                work_item_id=WI_STUB,
                run_id=RUN_STUB,
                title="t",
                description="d",
            )
        f["conn"].close()
        self.assertFalse(fr.ok)
        self.assertEqual(fr.exit_code, 7)
        self.assertEqual(len(fr.accounts_tried), 1)
        self.assertFalse(fr.exhausted_accounts)
        self.assertFalse(fr.max_tries_reached)


class QwenCliRunnerTimeoutTest(unittest.TestCase):
    def setUp(self) -> None:
        reload_config_with_account_keys(1)
        self._fd, self._raw = tempfile.mkstemp(prefix="factory_ut_qwen_", suffix=".db")
        os.close(self._fd)
        self.db_path = Path(self._raw)

    def tearDown(self) -> None:
        os.environ.pop("FACTORY_QWEN_TIMEOUT_SEC", None)
        reload_single_test_account()
        try:
            self.db_path.unlink(missing_ok=True)
        except OSError:
            pass

    def test_timeout_marks_failed_result(self) -> None:
        os.environ["FACTORY_QWEN_DRY_RUN"] = "0"
        os.environ["FACTORY_QWEN_TIMEOUT_SEC"] = "2"

        def _raise(*_a, **_k):
            raise TimeoutExpired(cmd=["qwen"], timeout=2)

        f = wire(self.db_path)
        _stub_wi_and_run(f["conn"])
        with patch("factory.qwen_cli_runner.subprocess.run", side_effect=_raise):
            fr = qwen_cli_runner.run_qwen_cli(
                conn=f["conn"],
                account_manager=f["accounts"],
                logger=f["logger"],
                work_item_id=WI_STUB,
                run_id=RUN_STUB,
                title="t",
                description="d",
            )
        f["conn"].close()
        self.assertFalse(fr.ok)
        self.assertIn("timeout", (fr.error_message or "").lower())
        self.assertEqual(fr.exit_code, -1)
        self.assertEqual(len(fr.accounts_tried), 1)


class QwenCliRunnerRateLimitAndPoolTest(unittest.TestCase):
    def setUp(self) -> None:
        self._fd, self._raw = tempfile.mkstemp(prefix="factory_ut_qwen_", suffix=".db")
        os.close(self._fd)
        self.db_path = Path(self._raw)

    def tearDown(self) -> None:
        os.environ.pop("FACTORY_QWEN_MAX_ACCOUNT_TRIES", None)
        reload_single_test_account()
        try:
            self.db_path.unlink(missing_ok=True)
        except OSError:
            pass

    def test_rate_limit_max_tries_reached(self) -> None:
        """Четыре слота, max_iter=4, каждый ответ «429» — исчерпание итераций без успеха."""
        reload_config_with_account_keys(4)
        os.environ["FACTORY_QWEN_DRY_RUN"] = "0"
        os.environ["FACTORY_QWEN_MAX_ACCOUNT_TRIES"] = "4"

        rate_limited = CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr=b"HTTP 429 too many requests"
        )

        def _always_429(*_a, **_k):
            return rate_limited

        f = wire(self.db_path)
        _stub_wi_and_run(f["conn"])
        with patch("factory.qwen_cli_runner.subprocess.run", side_effect=_always_429):
            fr = qwen_cli_runner.run_qwen_cli(
                conn=f["conn"],
                account_manager=f["accounts"],
                logger=f["logger"],
                work_item_id=WI_STUB,
                run_id=RUN_STUB,
                title="t",
                description="d",
            )
        f["conn"].close()
        self.assertFalse(fr.ok)
        self.assertTrue(fr.max_tries_reached)
        self.assertFalse(fr.exhausted_accounts)
        self.assertEqual(len(fr.accounts_tried), 4)

    def test_rate_limit_all_accounts_exhausted_before_max_iter(self) -> None:
        """Три слота, все уходят в cooldown — ``AccountExhaustedError`` до исчерпания max_iter."""
        reload_config_with_account_keys(3)
        os.environ["FACTORY_QWEN_DRY_RUN"] = "0"
        os.environ["FACTORY_QWEN_MAX_ACCOUNT_TRIES"] = "20"

        rate_limited = CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr=b"quota exceeded"
        )

        def _always_rl(*_a, **_k):
            return rate_limited

        f = wire(self.db_path)
        _stub_wi_and_run(f["conn"])
        with patch("factory.qwen_cli_runner.subprocess.run", side_effect=_always_rl):
            fr = qwen_cli_runner.run_qwen_cli(
                conn=f["conn"],
                account_manager=f["accounts"],
                logger=f["logger"],
                work_item_id=WI_STUB,
                run_id=RUN_STUB,
                title="t",
                description="d",
            )
        f["conn"].close()
        self.assertFalse(fr.ok)
        self.assertTrue(fr.exhausted_accounts)
        self.assertFalse(fr.max_tries_reached)
        self.assertEqual(len(fr.accounts_tried), 3)


class QwenCliRunnerRateLimitDetectionTest(unittest.TestCase):
    def test_looks_rate_limited_detects_known_markers(self) -> None:
        self.assertTrue(looks_rate_limited("HTTP 429 too many requests"))
        self.assertTrue(looks_rate_limited("Quota exceeded for this project"))
        self.assertTrue(looks_rate_limited("RESOURCE EXHAUSTED"))

    def test_looks_rate_limited_false_for_regular_error(self) -> None:
        self.assertFalse(looks_rate_limited(""))
        self.assertFalse(looks_rate_limited("build failed: missing target"))


class QwenCliRunnerMarkRateLimitedTest(unittest.TestCase):
    def setUp(self) -> None:
        reload_config_with_account_keys(2)
        self._fd, self._raw = tempfile.mkstemp(prefix="factory_ut_qwen_", suffix=".db")
        os.close(self._fd)
        self.db_path = Path(self._raw)

    def tearDown(self) -> None:
        os.environ.pop("FACTORY_QWEN_MAX_ACCOUNT_TRIES", None)
        reload_single_test_account()
        try:
            self.db_path.unlink(missing_ok=True)
        except OSError:
            pass

    def test_run_qwen_cli_marks_account_cooling_down_on_rate_limit(self) -> None:
        os.environ["FACTORY_QWEN_DRY_RUN"] = "0"
        os.environ["FACTORY_QWEN_MAX_ACCOUNT_TRIES"] = "1"

        f = wire(self.db_path)
        _stub_wi_and_run(f["conn"])
        rate_limited = CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr=b"HTTP 429 too many requests"
        )

        with patch("factory.qwen_cli_runner.subprocess.run", return_value=rate_limited):
            fr = qwen_cli_runner.run_qwen_cli(
                conn=f["conn"],
                account_manager=f["accounts"],
                logger=f["logger"],
                work_item_id=WI_STUB,
                run_id=RUN_STUB,
                title="t",
                description="d",
            )

        first_account = fr.accounts_tried[0]
        row = f["conn"].execute(
            "SELECT account_status, last_error FROM api_accounts WHERE id = ?",
            (first_account,),
        ).fetchone()
        f["conn"].close()

        self.assertFalse(fr.ok)
        self.assertTrue(fr.max_tries_reached)
        self.assertEqual(row["account_status"], "cooling_down")
        self.assertIn("429", row["last_error"])


if __name__ == "__main__":
    unittest.main()
