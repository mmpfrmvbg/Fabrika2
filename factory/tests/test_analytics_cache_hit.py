"""Analytics: dry-run cache-hit runs must be excluded from aggregates."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from factory.api_server import api_analytics
from factory.db import gen_id, init_db


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class AnalyticsCacheHitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(tempfile.mkstemp(prefix="factory_analytics_cache_hit_", suffix=".db")[1])
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

        wi_id = gen_id("atm")
        conn.execute(
            """
            INSERT INTO work_items (
                id, parent_id, root_id, kind, title, description, status,
                creator_role, owner_role, planning_depth, retry_count,
                created_at, updated_at
            )
            VALUES (?, NULL, ?, 'atom', 'A', '', 'done',
                    'creator', 'forge', 0, 0, ?, ?)
            """,
            (wi_id, wi_id, _iso(now - timedelta(hours=2)), _iso(now - timedelta(hours=1))),
        )

        def run_row(*, role: str, started: datetime, finished: datetime, dry_run: int) -> None:
            conn.execute(
                """
                INSERT INTO runs (
                    id, work_item_id, agent_id, role, run_type, status,
                    started_at, finished_at, dry_run
                )
                VALUES (?, ?, ?, ?, 'implement', 'completed', ?, ?, ?)
                """,
                (
                    gen_id("run"),
                    wi_id,
                    f"agent_{role}",
                    role,
                    _iso(started),
                    _iso(finished),
                    dry_run,
                ),
            )

        # Wet forge run: should be counted in stage metrics.
        run_row(
            role="forge",
            started=now - timedelta(minutes=20),
            finished=now - timedelta(minutes=10),
            dry_run=0,
        )
        # Cache-hit dry-run forge run: should be excluded from stage aggregates.
        run_row(
            role="forge",
            started=now - timedelta(minutes=9),
            finished=now - timedelta(minutes=9) + timedelta(seconds=1),
            dry_run=1,
        )
        # Dry-run reviewer run: should be excluded from reviewer stage count too.
        run_row(
            role="reviewer",
            started=now - timedelta(minutes=8),
            finished=now - timedelta(minutes=8) + timedelta(seconds=1),
            dry_run=1,
        )

        conn.commit()
        conn.close()

    def test_dry_run_runs_excluded_from_stage_counts_and_timings(self) -> None:
        out = api_analytics(period="24h")

        self.assertEqual(out["stages"]["forge"]["count"], 1)
        self.assertEqual(out["stages"]["forge"]["avg_duration_sec"], 600)
        self.assertEqual(out["stages"]["review"]["count"], 0)

    def test_cache_hit_rate_counts_dry_run_forge_runs(self) -> None:
        out = api_analytics(period="24h")

        # forge implement runs: 2 total, 1 cache-hit dry-run => 0.5
        self.assertEqual(out["cache_hit_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
