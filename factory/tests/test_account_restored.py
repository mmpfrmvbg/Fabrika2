"""
Юнит: истёкший cooldown → ``AccountManager._expire_cooldowns`` → ``active`` + ``ACCOUNT_RESTORED`` в event_log.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from factory.composition import wire
from factory.models import EventType


class AccountRestoredTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.setdefault("FACTORY_API_KEY_1", "unit-test-account-restored")
        self._fd, self._path = tempfile.mkstemp(prefix="factory_ut_account_restored_", suffix=".db")
        os.close(self._fd)
        self.db_path = Path(self._path)

    def tearDown(self) -> None:
        try:
            self.db_path.unlink(missing_ok=True)
        except OSError:
            pass

    def test_cooling_down_past_cooldown_restores_and_logs_account_restored(self) -> None:
        f = wire(self.db_path)
        conn = f["conn"]
        am = f["accounts"]

        row = conn.execute("SELECT id FROM api_accounts LIMIT 1").fetchone()
        self.assertIsNotNone(row, "seed api_accounts")
        acc_id = row["id"]
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

        conn.execute(
            """
            UPDATE api_accounts
            SET account_status = 'cooling_down', cooldown_until = ?, last_error = 'quota'
            WHERE id = ?
            """,
            (past, acc_id),
        )
        conn.commit()

        am._expire_cooldowns()
        conn.commit()

        st = conn.execute(
            "SELECT account_status, cooldown_until FROM api_accounts WHERE id = ?",
            (acc_id,),
        ).fetchone()
        self.assertEqual(st["account_status"], "active")
        self.assertIsNone(st["cooldown_until"])

        ev = conn.execute(
            """
            SELECT payload FROM event_log
            WHERE event_type = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (EventType.ACCOUNT_RESTORED.value,),
        ).fetchone()
        self.assertIsNotNone(ev, "ожидался ACCOUNT_RESTORED в event_log")
        payload = json.loads(ev["payload"])
        self.assertEqual(payload.get("account_id"), acc_id)
        self.assertIn("restored_at", payload)
        self.assertEqual(payload.get("previous_cooldown_until"), past)
        self.assertIn("next_available_after", payload)


if __name__ == "__main__":
    unittest.main()
