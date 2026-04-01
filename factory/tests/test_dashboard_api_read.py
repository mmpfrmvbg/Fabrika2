"""GET /api/tasks, /api/tree, /api/tasks/<id>."""

from __future__ import annotations

import sqlite3

from factory.dashboard_api_read import (
    api_task_detail,
    api_tasks_list,
    api_tree_nested,
    api_work_items_list,
)
from factory.dashboard_live_read import api_work_item_subtree
from factory.db import gen_id, init_db


def test_api_tasks_list_filter_kind(tmp_path) -> None:
    db = tmp_path / "api_tasks.db"
    conn = init_db(db)
    now = "2026-03-30T12:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        )
        VALUES ('a1', NULL, 'a1', 'atom', 'A', '', 'ready_for_work', 'c', 'p', 1, 1, ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        )
        VALUES ('v1', NULL, 'v1', 'vision', 'V', '', 'draft', 'c', 'p', 0, 1, ?, ?)
        """,
        (now, now),
    )
    conn.commit()
    conn.close()

    ro = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    out = api_tasks_list(ro, kind="atom")
    ro.close()
    assert len(out["items"]) == 1
    assert out["items"][0]["id"] == "a1"
    assert out["items"][0]["kind"] == "atom"


def test_api_tree_nested_and_detail(tmp_path) -> None:
    db = tmp_path / "tree.db"
    conn = init_db(db)
    now = "2026-03-30T12:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        )
        VALUES ('root', NULL, 'root', 'vision', 'V', '', 'planned', 'c', 'p', 0, 1, ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        )
        VALUES ('ch1', 'root', 'root', 'atom', 'A', '', 'ready_for_work', 'c', 'p', 1, 1, ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO work_item_files (id, work_item_id, path, intent, description, required)
        VALUES (?, 'ch1', 'factory/hello_qwen.py', 'modify', 'x', 1)
        """,
        (gen_id("wif"),),
    )
    conn.commit()
    conn.close()

    ro = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    tree = api_tree_nested(ro)
    assert len(tree["roots"]) == 1
    assert tree["roots"][0]["children"][0]["id"] == "ch1"

    det = api_task_detail(ro, "ch1")
    assert det["work_item"]["id"] == "ch1"
    assert len(det["work_item"]["files"]) == 1
    ro.close()


def test_api_work_items_list_includes_files(tmp_path) -> None:
    db = tmp_path / "wi_list.db"
    conn = init_db(db)
    now = "2026-03-30T12:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        )
        VALUES ('atm1', NULL, 'atm1', 'atom', 'A', '', 'draft', 'c', 'p', 1, 5, ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO work_item_files (id, work_item_id, path, intent, description, required)
        VALUES (?, 'atm1', 'factory/x.py', 'modify', 'd', 1)
        """,
        (gen_id("wif"),),
    )
    conn.commit()
    conn.close()

    ro = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    out = api_work_items_list(ro, kind="atom", status="draft")
    ro.close()
    assert out["count"] == 1
    assert out["items"][0]["priority"] == 5
    assert len(out["items"][0]["files"]) == 1
    assert out["items"][0]["files"][0]["path"] == "factory/x.py"


def test_api_work_item_subtree(tmp_path) -> None:
    db = tmp_path / "subtree.db"
    conn = init_db(db)
    now = "2026-03-30T12:00:00.000000Z"
    for wid, pid, k, depth in [
        ("v", None, "vision", 0),
        ("e", "v", "epic", 1),
        ("a", "e", "atom", 2),
    ]:
        conn.execute(
            """
            INSERT INTO work_items (
                id, parent_id, root_id, kind, title, description, status,
                creator_role, owner_role, planning_depth, priority, created_at, updated_at
            )
            VALUES (?, ?, 'v', ?, 't', '', 'draft', 'c', 'p', ?, 1, ?, ?)
            """,
            (wid, pid, k, depth, now, now),
        )
    conn.commit()
    conn.close()

    ro = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    sub = api_work_item_subtree(ro, "e")
    ro.close()
    assert sub.get("error") is None
    assert len(sub["roots"]) == 1
    assert sub["roots"][0]["id"] == "e"
    assert len(sub["roots"][0]["children"]) == 1
    assert sub["roots"][0]["children"][0]["kind"] == "atom"
