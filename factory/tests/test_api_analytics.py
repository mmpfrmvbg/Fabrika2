"""GET /api/analytics — структура JSON и фильтр period."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from factory.api_server import api_analytics
from factory.db import gen_id, init_db
from factory.models import EventType, WorkItemStatus


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class ApiAnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(tempfile.mkstemp(prefix="factory_analytics_", suffix=".db")[1])
        self.prev_db = os.environ.get("FACTORY_DB")
        init_db(self.path).close()
        os.environ["FACTORY_DB"] = str(self.path)
        self._seed()

    def tearDown(self) -> None:
        if self.prev_db is None:
            os.environ.pop("FACTORY_DB", None)
        else:
            os.environ["FACTORY_DB"] = self.prev_db
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def _seed(self) -> None:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        now = datetime.now(timezone.utc)
        acc = conn.execute("SELECT id FROM api_accounts LIMIT 1").fetchone()["id"]

        v_new_1 = gen_id("vis")
        v_new_2 = gen_id("vis")
        v_new_3 = gen_id("vis")
        v_old = gen_id("vis")
        root = v_new_1

        def wi(
            wid: str,
            *,
            kind: str,
            status: str,
            created: datetime,
            parent: str | None,
            root_id: str,
            retry: int = 0,
            updated: datetime | None = None,
        ) -> None:
            conn.execute(
                """
                INSERT INTO work_items (
                    id, parent_id, root_id, kind, title, description, status,
                    creator_role, owner_role, planning_depth, retry_count,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, '', ?, 'creator', 'forge', 0, ?, ?, ?)
                """,
                (
                    wid,
                    parent,
                    root_id,
                    kind,
                    f"T-{wid[-6:]}",
                    status,
                    retry,
                    _iso(created),
                    _iso(updated or created),
                ),
            )

        # Когорта за «последние 24ч» (created_at)
        wi(
            v_new_1,
            kind="vision",
            status=WorkItemStatus.DONE.value,
            created=now - timedelta(hours=2),
            parent=None,
            root_id=v_new_1,
        )
        wi(
            v_new_2,
            kind="vision",
            status=WorkItemStatus.DRAFT.value,
            created=now - timedelta(hours=5),
            parent=None,
            root_id=v_new_2,
        )
        wi(
            v_new_3,
            kind="vision",
            status=WorkItemStatus.CANCELLED.value,
            created=now - timedelta(hours=1),
            parent=None,
            root_id=v_new_3,
        )
        wi(
            v_old,
            kind="vision",
            status=WorkItemStatus.PLANNED.value,
            created=now - timedelta(days=400),
            parent=None,
            root_id=v_old,
        )

        a_done = gen_id("atm")
        a_ip = gen_id("atm")
        a_fail = gen_id("atm")
        wi(
            a_done,
            kind="atom",
            status=WorkItemStatus.DONE.value,
            created=now - timedelta(hours=10),
            parent=root,
            root_id=root,
            retry=0,
            updated=now - timedelta(minutes=30),
        )
        wi(
            a_ip,
            kind="atom",
            status=WorkItemStatus.IN_PROGRESS.value,
            created=now - timedelta(hours=8),
            parent=root,
            root_id=root,
            retry=1,
        )
        wi(
            a_fail,
            kind="atom",
            status=WorkItemStatus.CANCELLED.value,
            created=now - timedelta(hours=6),
            parent=root,
            root_id=root,
        )

        t0 = now - timedelta(hours=3)
        t1 = now - timedelta(hours=2, minutes=50)
        for et, ts in [
            (EventType.FORGE_STARTED.value, t0),
            (EventType.JUDGE_APPROVED.value, t1),
        ]:
            conn.execute(
                """
                INSERT INTO event_log (
                    event_time, event_type, entity_type, entity_id,
                    work_item_id, severity, message, payload
                )
                VALUES (?, ?, 'work_item', ?, ?, 'info', 'test', '{}')
                """,
                (_iso(ts), et, a_done, a_done),
            )

        def run_row(
            rid: str,
            aid: str,
            role: str,
            *,
            started: datetime,
            finished: datetime,
            status: str = "completed",
        ) -> None:
            conn.execute(
                """
                INSERT INTO runs (
                    id, work_item_id, agent_id, role, run_type, status,
                    started_at, finished_at
                )
                VALUES (?, ?, ?, ?, 'implement', ?, ?, ?)
                """,
                (
                    rid,
                    aid,
                    f"agent_{role}" if role != "reviewer" else "agent_reviewer",
                    role,
                    status,
                    _iso(started),
                    _iso(finished),
                ),
            )

        run_row(gen_id("run"), a_done, "forge", started=now - timedelta(hours=3), finished=now - timedelta(hours=2, minutes=55))
        run_row(
            gen_id("run"),
            a_done,
            "reviewer",
            started=now - timedelta(hours=2, minutes=54),
            finished=now - timedelta(hours=2, minutes=52),
        )
        run_row(gen_id("run"), a_done, "judge", started=now - timedelta(hours=2, minutes=51), finished=now - timedelta(hours=2, minutes=50))

        conn.execute(
            """
            INSERT INTO api_usage (account_id, tokens_in, tokens_out, request_count, created_at)
            VALUES (?, 100, 40, 2, ?)
            """,
            (acc, _iso(now - timedelta(hours=1))),
        )

        conn.commit()
        conn.close()

    def test_analytics_structure_24h(self) -> None:
        out = api_analytics(period="24h")
        self.assertEqual(out["period"], "24h")
        self.assertIn("visions", out)
        self.assertIn("atoms", out)
        self.assertIn("stages", out)
        self.assertIn("llm", out)
        self.assertIn("throughput", out)

        self.assertEqual(out["visions"]["total"], 3)
        self.assertEqual(out["visions"]["completed"], 1)
        self.assertEqual(out["visions"]["failed"], 1)

        self.assertEqual(out["atoms"]["total"], 3)
        self.assertEqual(out["atoms"]["completed"], 1)
        self.assertEqual(out["atoms"]["failed"], 1)
        self.assertEqual(out["atoms"]["first_pass_rate"], 1.0)
        self.assertGreaterEqual(out["atoms"]["avg_cycle_time_sec"], 0)

        for k in ("forge", "review", "judge"):
            s = out["stages"][k]
            self.assertIn("avg_duration_sec", s)
            self.assertIn("count", s)
            self.assertIn("fail_rate", s)

        self.assertGreaterEqual(out["llm"]["total_calls"], 2)
        self.assertIsInstance(out["throughput"], list)

    def test_analytics_period_all_includes_old_vision(self) -> None:
        out = api_analytics(period="all")
        self.assertEqual(out["period"], "all")
        self.assertEqual(out["visions"]["total"], 4)


if __name__ == "__main__":
    unittest.main()
