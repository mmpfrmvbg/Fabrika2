"""GET /api/tasks, /api/tree, /api/tasks/<id>."""

from __future__ import annotations

import json
import sqlite3
import threading
from http.server import HTTPServer
from urllib.request import urlopen

from factory import dashboard_api
from factory.dashboard_api_read import (
    api_task_detail,
    api_tasks_list,
    api_tree_nested,
    api_work_items_list,
)
from factory.dashboard_live_read import api_work_item_subtree
from factory.db import gen_id, init_db


def _start_server() -> tuple[HTTPServer, threading.Thread, str]:
    server = HTTPServer(("127.0.0.1", 0), dashboard_api.DashboardRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, f"http://{host}:{port}"


def _get_json(url: str) -> tuple[int, dict]:
    with urlopen(url) as resp:  # noqa: S310 - local test server
        return resp.status, json.loads(resp.read().decode("utf-8"))


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


def test_dashboard_runs_effective_endpoint_returns_200_and_structure(monkeypatch, tmp_path) -> None:
    db = tmp_path / "runs_effective.db"
    conn = init_db(db)
    now = "2026-04-04T12:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        )
        VALUES ('atom_runs_effective', NULL, 'atom_runs_effective', 'atom', 'A', '', 'in_progress', 'c', 'forge', 1, 1, ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO runs (
            id, work_item_id, agent_id, role, run_type, status, started_at, finished_at, source_run_id, dry_run
        )
        VALUES ('run_src_eff', 'atom_runs_effective', 'agent_forge', 'forge', 'implement', 'done', ?, ?, NULL, 0)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO runs (
            id, work_item_id, agent_id, role, run_type, status, started_at, finished_at, source_run_id, dry_run
        )
        VALUES ('run_cache_eff', 'atom_runs_effective', 'agent_forge', 'forge', 'implement', 'done', ?, ?, 'run_src_eff', 1)
        """,
        (now, now),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("FACTORY_DB", str(db))
    server, thread, base = _start_server()
    try:
        status, body = _get_json(f"{base}/api/runs_effective?run_id=run_cache_eff")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 200
    assert isinstance(body, dict)
    assert set(body.keys()) == {"effective_run_id"}
    assert body["effective_run_id"] == "run_src_eff"


def test_dashboard_runs_detail_endpoint_returns_200_for_run_id(monkeypatch, tmp_path) -> None:
    db = tmp_path / "runs_detail.db"
    conn = init_db(db)
    now = "2026-04-04T12:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority, created_at, updated_at
        )
        VALUES ('atom_runs_detail', NULL, 'atom_runs_detail', 'atom', 'A', '', 'in_progress', 'c', 'forge', 1, 1, ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO runs (
            id, work_item_id, agent_id, role, run_type, status, started_at, finished_at, source_run_id, dry_run
        )
        VALUES ('run_detail_1', 'atom_runs_detail', 'agent_forge', 'forge', 'implement', 'done', ?, ?, NULL, 0)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO run_steps (
            id, run_id, step_no, step_kind, status, summary, payload, created_at
        )
        VALUES (?, 'run_detail_1', 1, 'apply', 'done', 'applied changes', '{}', ?)
        """,
        (gen_id("rs"), now),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("FACTORY_DB", str(db))
    server, thread, base = _start_server()
    try:
        status, body = _get_json(f"{base}/api/runs_detail?run_id=run_detail_1")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 200
    assert body["work_item_id"] == "atom_runs_detail"
    assert isinstance(body["runs"], list)
    assert len(body["runs"]) == 1
    assert body["runs"][0]["id"] == "run_detail_1"
