from __future__ import annotations

import sqlite3

from factory.guards import Guards


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE work_items (
            id TEXT PRIMARY KEY,
            status TEXT,
            retry_count INTEGER,
            max_retries INTEGER,
            parent_id TEXT
        );
        CREATE TABLE work_item_files (
            id TEXT PRIMARY KEY,
            work_item_id TEXT,
            path TEXT,
            intent TEXT
        );
        CREATE TABLE file_locks (
            id TEXT PRIMARY KEY,
            path TEXT,
            work_item_id TEXT,
            released_at TEXT
        );
        CREATE TABLE comments (
            id TEXT PRIMARY KEY,
            work_item_id TEXT,
            author_role TEXT,
            comment_type TEXT,
            created_at TEXT
        );
        CREATE TABLE decisions (
            id TEXT PRIMARY KEY,
            work_item_id TEXT,
            verdict TEXT,
            created_at TEXT
        );
        """
    )
    return conn


def test_guard_has_children_true_and_false() -> None:
    conn = _conn()
    conn.execute("INSERT INTO work_items(id, status, retry_count, max_retries, parent_id) VALUES ('p', 'draft', 0, 3, NULL)")
    g = Guards(conn)

    ok, _ = g.guard_has_children("p")
    assert ok is False

    conn.execute("INSERT INTO work_items(id, status, retry_count, max_retries, parent_id) VALUES ('c1', 'draft', 0, 3, 'p')")
    ok, _ = g.guard_has_children("p")
    assert ok is True


def test_guard_can_retry_and_over_retry_limit() -> None:
    conn = _conn()
    conn.execute("INSERT INTO work_items(id, status, retry_count, max_retries, parent_id) VALUES ('w1', 'failed', 1, 3, NULL)")
    conn.execute("INSERT INTO work_items(id, status, retry_count, max_retries, parent_id) VALUES ('w2', 'failed', 3, 3, NULL)")
    g = Guards(conn)

    ok1, _ = g.guard_can_retry("w1")
    ok2, _ = g.guard_can_retry("w2")
    over1, _ = g.guard_over_retry_limit("w1")
    over2, _ = g.guard_over_retry_limit("w2")

    assert ok1 is True
    assert ok2 is False
    assert over1 is False
    assert over2 is True


def test_guard_files_lockable() -> None:
    conn = _conn()
    g = Guards(conn)
    conn.execute("INSERT INTO work_item_files(id, work_item_id, path, intent) VALUES ('f1', 'w1', 'src/a.py', 'modify')")

    ok_free, _ = g.guard_files_lockable("w1")
    assert ok_free is True

    conn.execute("INSERT INTO file_locks(id, path, work_item_id, released_at) VALUES ('l1', 'src/a.py', 'w2', NULL)")
    ok_locked, _ = g.guard_files_lockable("w1")
    assert ok_locked is False
