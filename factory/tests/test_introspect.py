"""Тесты self-improvement loop: factory_introspect + API improvements."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from factory.api_server import app
from factory.db import ensure_improvement_candidates_schema, gen_id, init_db
from factory.factory_introspect import FactoryIntrospector
from factory.logging import FactoryLogger
from fastapi.testclient import TestClient


def _mk_db() -> Path:
    d = tempfile.mkdtemp()
    return Path(d) / "t.db"


class TestIntrospect(unittest.TestCase):
    def test_collect_failure_clusters(self) -> None:
        db = _mk_db()
        init_db(db)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        et = "forge.failed.unit"
        for i in range(5):
            conn.execute(
                """
                INSERT INTO event_log (
                    event_time, event_type, entity_type, entity_id, severity, message,
                    work_item_id, actor_role
                )
                VALUES (
                    datetime('now', ?), ?, 'work_item', ?, 'error', ?,
                    ?, 'forge'
                )
                """,
                (f"-{i} minutes", et, gen_id("wi"), f"m{i}", gen_id("wi")),
            )
        conn.commit()
        intro = FactoryIntrospector()
        cands = intro.collect_signals(conn)
        conn.close()
        self.assertTrue(any(c["source_type"] == "failure_cluster" for c in cands))

    def test_collect_retry_hotspots(self) -> None:
        db = _mk_db()
        init_db(db)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        wid = gen_id("wi")
        conn.execute(
            """
            INSERT INTO work_items (id, root_id, kind, title, status, creator_role, owner_role)
            VALUES (?, ?, 'atom', 'hot', 'ready_for_work', 'creator', 'creator')
            """,
            (wid, wid),
        )
        conn.execute(
            "INSERT OR IGNORE INTO agents (id, role, active) VALUES ('agent_forge', 'forge', 1)"
        )
        for _ in range(4):
            conn.execute(
                """
                INSERT INTO runs (id, work_item_id, agent_id, role, run_type, status)
                VALUES (?, ?, 'agent_forge', 'forge', 'implement', 'completed')
                """,
                (gen_id("run"), wid),
            )
        conn.commit()
        intro = FactoryIntrospector()
        cands = intro.collect_signals(conn)
        conn.close()
        self.assertTrue(any(c["source_type"] == "retry_hotspot" for c in cands))

    def test_deduplication(self) -> None:
        db = _mk_db()
        init_db(db)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        ensure_improvement_candidates_schema(conn)
        cid = gen_id("ic")
        conn.execute(
            """
            INSERT INTO improvement_candidates (
                id, source_type, source_ref, title, description, evidence,
                fix_target, frequency, severity_score, impact_score, confidence, status, risk_level
            )
            VALUES (?, 'failure_cluster', 'x.err', 't', 'd', '{}', 'code', 1, 0.5, 0.5, 0.5, 'proposed', 'low')
            """,
            (cid,),
        )
        conn.commit()
        intro = FactoryIntrospector()
        fresh = intro.deduplicate(
            conn,
            [
                {
                    "source_type": "failure_cluster",
                    "source_ref": "x.err",
                    "title": "again",
                    "description": "d",
                    "evidence": {},
                    "fix_target": "code",
                    "frequency": 3,
                    "severity_score": 0.5,
                    "impact_score": 0.5,
                    "confidence": 0.5,
                }
            ],
        )
        self.assertEqual(fresh, [])
        row = conn.execute(
            "SELECT frequency FROM improvement_candidates WHERE id = ?", (cid,)
        ).fetchone()
        self.assertEqual(int(row["frequency"]), 2)
        conn.close()

    def test_auto_approve(self) -> None:
        db = _mk_db()
        init_db(db)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        ensure_improvement_candidates_schema(conn)
        cid = gen_id("ic")
        conn.execute(
            """
            INSERT INTO improvement_candidates (
                id, source_type, source_ref, title, description, evidence,
                fix_target, frequency, severity_score, impact_score, confidence,
                status, risk_level, created_at
            )
            VALUES (?, 'manual', NULL, 't', 'd', '{}', 'code', 5, 0.9, 0.9, 0.9,
                    'proposed', 'low', datetime('now', '-2 hours'))
            """,
            (cid,),
        )
        conn.commit()
        intro = FactoryIntrospector()
        intro.auto_approve_low_risk(conn, None)
        conn.commit()
        st = conn.execute("SELECT status FROM improvement_candidates WHERE id = ?", (cid,)).fetchone()[
            "status"
        ]
        self.assertEqual(st, "approved")
        conn.close()

    def test_convert_to_vision(self) -> None:
        db = _mk_db()
        init_db(db)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        ensure_improvement_candidates_schema(conn)
        logger = FactoryLogger(conn)
        cid = gen_id("ic")
        conn.execute(
            """
            INSERT INTO improvement_candidates (
                id, source_type, source_ref, title, description, evidence,
                fix_target, frequency, severity_score, impact_score, confidence,
                status, risk_level
            )
            VALUES (?, 'manual', NULL, 'Vis title', 'Vis body', '{}', 'code', 1, 0.5, 0.5, 0.5,
                    'approved', 'low')
            """,
            (cid,),
        )
        conn.commit()
        intro = FactoryIntrospector()
        vid = intro.convert_one(conn, cid, logger)
        conn.commit()
        self.assertTrue(vid.startswith("vis_"))
        row = conn.execute(
            "SELECT status, vision_id FROM improvement_candidates WHERE id = ?", (cid,)
        ).fetchone()
        self.assertEqual(row["status"], "converted")
        self.assertEqual(row["vision_id"], vid)
        wi = conn.execute("SELECT kind, title FROM work_items WHERE id = ?", (vid,)).fetchone()
        self.assertEqual(wi["kind"], "vision")
        self.assertEqual(wi["title"], "Vis title")
        conn.close()

    def test_high_risk_not_auto_approved(self) -> None:
        db = _mk_db()
        init_db(db)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        ensure_improvement_candidates_schema(conn)
        cid = gen_id("ic")
        conn.execute(
            """
            INSERT INTO improvement_candidates (
                id, source_type, source_ref, title, description, evidence,
                fix_target, frequency, severity_score, impact_score, confidence,
                status, risk_level, created_at
            )
            VALUES (?, 'manual', NULL, 't', 'd', '{}', 'prompt', 1, 0.9, 0.9, 0.9,
                    'proposed', 'high', datetime('now', '-3 hours'))
            """,
            (cid,),
        )
        conn.commit()
        intro = FactoryIntrospector()
        intro.auto_approve_low_risk(conn, None)
        conn.commit()
        st = conn.execute("SELECT status FROM improvement_candidates WHERE id = ?", (cid,)).fetchone()[
            "status"
        ]
        self.assertEqual(st, "proposed")
        conn.close()

    def test_api_improvements(self) -> None:
        db = _mk_db()
        init_db(db)
        import factory.api_server as api_mod

        prev = api_mod._db_path  # type: ignore[attr-defined]

        def _p() -> Path:
            return db

        api_mod._db_path = _p  # type: ignore[assignment]
        try:
            client = TestClient(app)
            conn = sqlite3.connect(str(db))
            ensure_improvement_candidates_schema(conn)
            conn.execute(
                """
                INSERT INTO improvement_candidates (
                    id, source_type, source_ref, title, description, evidence,
                    fix_target, frequency, severity_score, impact_score, confidence,
                    status, risk_level
                )
                VALUES ('ic_test1', 'manual', NULL, 'Api', 'Desc', '{}', 'code', 1, 0.8, 0.8, 0.8,
                        'proposed', 'low')
                """
            )
            conn.commit()
            conn.close()
            r = client.get("/api/improvements")
            self.assertEqual(r.status_code, 200)
            data = r.json()
            self.assertIn("candidates", data)
            self.assertIn("stats", data)
            self.assertTrue(any(c.get("id") == "ic_test1" for c in data["candidates"]))
        finally:
            api_mod._db_path = prev  # type: ignore[assignment]

    def test_api_approve_reject(self) -> None:
        db = _mk_db()
        init_db(db)
        import factory.api_server as api_mod

        prev = api_mod._db_path  # type: ignore[attr-defined]

        def _p() -> Path:
            return db

        api_mod._db_path = _p  # type: ignore[assignment]
        try:
            client = TestClient(app)
            conn = sqlite3.connect(str(db))
            ensure_improvement_candidates_schema(conn)
            conn.execute(
                """
                INSERT INTO improvement_candidates (
                    id, source_type, source_ref, title, description, evidence,
                    fix_target, frequency, severity_score, impact_score, confidence,
                    status, risk_level
                )
                VALUES ('ic_ar1', 'manual', NULL, 'X', 'Y', '{}', 'code', 1, 0.5, 0.5, 0.5,
                        'proposed', 'high')
                """
            )
            conn.commit()
            conn.close()
            r1 = client.post("/api/improvements/ic_ar1/approve", json={"reviewed_by": "tester"})
            self.assertEqual(r1.status_code, 200)
            self.assertTrue(r1.json().get("ok"))
            r2 = client.post("/api/improvements/ic_ar1/reject", json={})
            # already approved — reject should 400
            self.assertEqual(r2.status_code, 400)
            conn = sqlite3.connect(str(db))
            conn.execute(
                """
                INSERT INTO improvement_candidates (
                    id, source_type, source_ref, title, description, evidence,
                    fix_target, frequency, severity_score, impact_score, confidence,
                    status, risk_level
                )
                VALUES ('ic_ar2', 'manual', NULL, 'A', 'B', '{}', 'code', 1, 0.5, 0.5, 0.5,
                        'proposed', 'low')
                """
            )
            conn.commit()
            conn.close()
            r3 = client.post("/api/improvements/ic_ar2/reject", json={})
            self.assertEqual(r3.status_code, 200)
        finally:
            api_mod._db_path = prev  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
