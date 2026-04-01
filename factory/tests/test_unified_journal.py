"""Unified journal read-model: merge event_log, run_steps, file_changes, comments."""

from __future__ import annotations

import json
import sqlite3

from factory.dashboard_unified_journal import (
    JournalFilters,
    api_journal_query,
    journal_count,
)
from factory.db import gen_id, init_db


def test_journal_merges_sources_and_stable_keys(tmp_path) -> None:
    db = tmp_path / "uj.db"
    conn = init_db(db)
    t0 = "2026-03-30T10:00:00.000000"
    t1 = "2026-03-30T10:00:01.000000"
    aid = gen_id("agent")
    wid = gen_id("wi")
    root = gen_id("root")
    conn.execute(
        """
        INSERT INTO agents (id, role, active) VALUES (?, 'forge', 1)
        """,
        (aid,),
    )
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        )
        VALUES (?, NULL, ?, 'vision', 'V', '', 'planned', 'c', 'p', 0, 1, ?, ?)
        """,
        (root, root, t0, t0),
    )
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        )
        VALUES (?, ?, ?, 'atom', 'A', '', 'in_progress', 'c', 'forge', 1, 1, ?, ?)
        """,
        (wid, root, root, t0, t0),
    )
    rid = gen_id("run")
    conn.execute(
        """
        INSERT INTO runs (
            id, work_item_id, agent_id, role, run_type, status, started_at, finished_at
        )
        VALUES (?, ?, ?, 'forge', 'implement', 'completed', ?, ?)
        """,
        (rid, wid, aid, t0, t1),
    )
    conn.execute(
        """
        INSERT INTO event_log (
            event_time, event_type, entity_type, entity_id, work_item_id, run_id,
            actor_role, severity, message, payload
        )
        VALUES (?, 'task.status_changed', 'work_item', ?, ?, NULL, 'system', 'info', 'x',
                ?)
        """,
        (
            t0,
            wid,
            wid,
            json.dumps({"from_status": "ready_for_work", "to_status": "in_progress"}),
        ),
    )
    sid = gen_id("step")
    conn.execute(
        """
        INSERT INTO run_steps (id, run_id, step_no, step_kind, status, summary, payload)
        VALUES (?, ?, 1, 'llm_reply', 'completed', 'ok', '{}')
        """,
        (sid, rid),
    )
    fcid = gen_id("fc")
    conn.execute(
        """
        INSERT INTO file_changes (
            id, work_item_id, run_id, path, change_type, old_hash, new_hash, diff_summary
        )
        VALUES (?, ?, ?, 'a.py', 'modify', 'h0', 'h1', '+1')
        """,
        (fcid, wid, rid),
    )
    cid = gen_id("com")
    conn.execute(
        """
        INSERT INTO comments (
            id, work_item_id, author_role, comment_type, body
        )
        VALUES (?, ?, 'reviewer', 'note', 'hello')
        """,
        (cid, wid),
    )
    conn.commit()
    conn.close()

    ro = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    out = api_journal_query(ro, JournalFilters(work_item_id=wid), limit=50, offset=0)
    ro.close()
    keys = {it["source_key"] for it in out["items"]}
    assert any(k.startswith("event:") for k in keys)
    assert any(k.startswith("run_step:") for k in keys)
    assert any(k.startswith("file_change:") for k in keys)
    assert any(k.startswith("comment:c:") for k in keys)
    kinds = {it["kind"] for it in out["items"]}
    assert "transition" in kinds
    assert "file_change" in kinds


def test_journal_subtree_root_filter(tmp_path) -> None:
    db = tmp_path / "uj2.db"
    conn = init_db(db)
    t = "2026-03-30T12:00:00.000000"
    epic = gen_id("epic")
    child = gen_id("ch")
    vision = gen_id("vis")
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        )
        VALUES (?, NULL, ?, 'vision', 'V', '', 'planned', 'c', 'p', 0, 1, ?, ?)
        """,
        (vision, vision, t, t),
    )
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        )
        VALUES (?, ?, ?, 'epic', 'E', '', 'planned', 'c', 'p', 1, 1, ?, ?)
        """,
        (epic, vision, vision, t, t),
    )
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        )
        VALUES (?, ?, ?, 'atom', 'A', '', 'draft', 'c', 'p', 2, 1, ?, ?)
        """,
        (child, epic, vision, t, t),
    )
    conn.execute(
        """
        INSERT INTO event_log (
            event_time, event_type, entity_type, entity_id, work_item_id,
            actor_role, severity, message
        )
        VALUES (?, 'task.created', 'work_item', ?, ?, 'system', 'info', 'c1')
        """,
        (t, child, child),
    )
    conn.commit()
    conn.close()
    ro = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    n_epic = journal_count(ro, JournalFilters(root_id=epic))
    n_vis = journal_count(ro, JournalFilters(root_id=vision))
    ro.close()
    assert n_epic == 1
    assert n_vis == 1


def test_journal_kind_role_filter(tmp_path) -> None:
    db = tmp_path / "uj3.db"
    conn = init_db(db)
    t = "2026-03-30T12:00:00.000000"
    wid = gen_id("w")
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        )
        VALUES (?, NULL, ?, 'atom', 'A', '', 'draft', 'c', 'p', 0, 1, ?, ?)
        """,
        (wid, wid, t, t),
    )
    conn.execute(
        """
        INSERT INTO event_log (
            event_time, event_type, entity_type, entity_id, work_item_id,
            actor_role, severity, message
        )
        VALUES (?, 'forge.started', 'work_item', ?, ?, 'forge', 'info', 'fs')
        """,
        (t, wid, wid),
    )
    conn.execute(
        """
        INSERT INTO event_log (
            event_time, event_type, entity_type, entity_id, work_item_id,
            actor_role, severity, message
        )
        VALUES (?, 'task.created', 'work_item', ?, ?, 'system', 'info', 'tc')
        """,
        (t, wid, wid),
    )
    conn.commit()
    conn.close()
    ro = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    out = api_journal_query(
        ro,
        JournalFilters(work_item_id=wid, kind="forge_started"),
        limit=20,
        offset=0,
    )
    ro.close()
    assert out["total"] == 1
    assert len(out["items"]) == 1
    assert out["items"][0]["kind"] == "forge_started"
