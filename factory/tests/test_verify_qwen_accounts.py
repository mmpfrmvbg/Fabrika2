"""Тесты verify_qwen_accounts: ротация слотов и детекция rate_limited маркеров."""

from __future__ import annotations

import unittest

from factory.tests.qwen_cli_test_env import (
    reload_config_with_account_keys,
    reload_single_test_account,
)
from factory.verify_qwen_accounts import run_account_manager_rotation
from factory.qwen_cli_runner import looks_rate_limited


class VerifyQwenAccountsRotationTest(unittest.TestCase):
    def setUp(self) -> None:
        reload_config_with_account_keys(3, prefix="ut_verify_")

    def tearDown(self) -> None:
        reload_single_test_account()

    def test_run_account_manager_rotation_switches_between_three_accounts(self) -> None:
        seen = run_account_manager_rotation()
        self.assertEqual(len(seen), 3)
        self.assertEqual(len(set(seen)), 3)


class VerifyQwenAccountsRateLimitedDetectionTest(unittest.TestCase):
    def test_looks_rate_limited_true_for_known_markers(self) -> None:
        positives = [
            "HTTP 429 too many requests",
            "quota exceeded for this account",
            "rate_limit reached",
            "RESOURCE EXHAUSTED",
            "traffic temporarily blocked",
        ]
        for text in positives:
            with self.subTest(text=text):
                self.assertTrue(looks_rate_limited(text))

    def test_looks_rate_limited_false_for_non_limit_errors(self) -> None:
        negatives = [
            "build failed: syntax error",
            "network timeout while connecting",
            "permission denied",
            "",
        ]
        for text in negatives:
            with self.subTest(text=text):
                self.assertFalse(looks_rate_limited(text))


if __name__ == "__main__":
    unittest.main()
