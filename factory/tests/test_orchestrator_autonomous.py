"""Автономный цикл: POST /api/visions → 5 тиков → атомы доходят до done (DRY_RUN)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from factory.api_server import create_vision, orchestrator_status, orchestrator_tick
from factory.db import init_db
from factory.orchestrator_core import wait_for_async_workers


def test_orchestrator_status_includes_queue_depths(monkeypatch) -> None:
    path = Path(tempfile.mkstemp(prefix="factory_orch_status_", suffix=".db")[1])
    try:
        monkeypatch.setenv("FACTORY_DB_PATH", str(path))
        init_db(path).close()

        st = orchestrator_status()
        assert isinstance(st, dict)
        assert "running" in st
        assert "queue_depths" in st
        qd = st["queue_depths"]
        assert isinstance(qd, dict)
        assert "forge_inbox" in qd and "review_inbox" in qd and "judge_inbox" in qd
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def test_create_vision_then_5_ticks_atoms_done(monkeypatch) -> None:
    path = Path(tempfile.mkstemp(prefix="factory_orch_ticks_", suffix=".db")[1])
    prev = os.environ.get("FACTORY_QWEN_DRY_RUN")
    try:
        monkeypatch.setenv("FACTORY_DB_PATH", str(path))
        os.environ["FACTORY_QWEN_DRY_RUN"] = "1"
        init_db(path).close()

        out = create_vision({"title": "Autonomous", "description": "DRY_RUN chain"})
        vid = out["id"]

        for _ in range(5):
            orchestrator_tick()
            wait_for_async_workers(timeout=30.0)

        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        atoms = conn.execute(
            "SELECT id, status FROM work_items WHERE root_id = ? AND kind = 'atom' ORDER BY created_at",
            (vid,),
        ).fetchall()
        assert len(atoms) >= 2
        assert all(a["status"] == "done" for a in atoms)

        # Rollup completion must reach the root as well.
        stories = conn.execute(
            "SELECT id, status FROM work_items WHERE root_id = ? AND kind = 'story'",
            (vid,),
        ).fetchall()
        epics = conn.execute(
            "SELECT id, status FROM work_items WHERE root_id = ? AND kind = 'epic'",
            (vid,),
        ).fetchall()
        vision = conn.execute(
            "SELECT id, status FROM work_items WHERE id = ?",
            (vid,),
        ).fetchone()
        assert vision is not None and vision["status"] == "done"
        assert all(s["status"] == "done" for s in stories)
        assert all(e["status"] == "done" for e in epics)
        conn.close()
    finally:
        if prev is None:
            os.environ.pop("FACTORY_QWEN_DRY_RUN", None)
        else:
            os.environ["FACTORY_QWEN_DRY_RUN"] = prev
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
