"""GET /api/stats и /api/orchestrator/heartbeat — поля orchestrator_* по event_log."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from factory.api_server import orchestrator_health, stats
from factory.db import init_db


class StatsOrchestratorHeartbeatTests(unittest.TestCase):
    def test_stats_no_orchestrator_events(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_orch_hb_", suffix=".db")[1])
        prev = os.environ.get("FACTORY_DB")
        try:
            init_db(path).close()
            os.environ["FACTORY_DB"] = str(path)
            out = stats()
            self.assertEqual(out.get("orchestrator_heartbeat_state"), "none")
            self.assertIsNone(out.get("orchestrator_last_event_time"))
        finally:
            if prev is None:
                os.environ.pop("FACTORY_DB", None)
            else:
                os.environ["FACTORY_DB"] = prev
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def test_stats_active_when_recent_orchestrator_event(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_orch_hb_", suffix=".db")[1])
        prev = os.environ.get("FACTORY_DB")
        try:
            init_db(path).close()
            conn = sqlite3.connect(str(path))
            conn.execute(
                """
                INSERT INTO event_log (
                    event_time, event_type, entity_type, entity_id,
                    severity, message, actor_role, payload
                )
                VALUES (?, 'orch.test', 'system', 'sys', 'info', 'tick', 'orchestrator', '{}')
                """,
                (datetime.now(timezone.utc).isoformat(),),
            )
            conn.commit()
            conn.close()
            os.environ["FACTORY_DB"] = str(path)
            out = stats()
            self.assertEqual(out.get("orchestrator_heartbeat_state"), "active")
            self.assertIsNotNone(out.get("orchestrator_seconds_since_last_event"))
            h = orchestrator_health()
            self.assertTrue(h.get("ok"))
            self.assertEqual(h.get("orchestrator_heartbeat_state"), "active")
        finally:
            if prev is None:
                os.environ.pop("FACTORY_DB", None)
            else:
                os.environ["FACTORY_DB"] = prev
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def test_stats_stale_when_old_orchestrator_event(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_orch_hb_", suffix=".db")[1])
        prev = os.environ.get("FACTORY_DB")
        try:
            init_db(path).close()
            conn = sqlite3.connect(str(path))
            conn.execute(
                """
                INSERT INTO event_log (
                    event_time, event_type, entity_type, entity_id,
                    severity, message, actor_role, payload
                )
                VALUES (?, 'orch.test', 'system', 'sys', 'info', 'old', 'orchestrator', '{}')
                """,
                ("2020-01-01T00:00:00+00:00",),
            )
            conn.commit()
            conn.close()
            os.environ["FACTORY_DB"] = str(path)
            out = stats()
            self.assertEqual(out.get("orchestrator_heartbeat_state"), "stale")
        finally:
            if prev is None:
                os.environ.pop("FACTORY_DB", None)
            else:
                os.environ["FACTORY_DB"] = prev
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
