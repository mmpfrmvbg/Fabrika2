"""Операции API для управления work_items: cancel/archive/delete (FSM + каскад)."""

from __future__ import annotations

import sqlite3
from typing import Any

from .logging import FactoryLogger
from .models import EventType, Role
from .fsm import StateMachine


def _post_order_ids(conn: sqlite3.Connection, root_id: str) -> list[str]:
    """Post-order: дети снизу вверх, root последним."""
    out: list[str] = []
    ch = conn.execute(
        "SELECT id FROM work_items WHERE parent_id = ? ORDER BY created_at",
        (root_id,),
    ).fetchall()
    for row in ch:
        out.extend(_post_order_ids(conn, row["id"]))
    out.append(root_id)
    return out


def _pre_order_ids(conn: sqlite3.Connection, root_id: str) -> list[str]:
    """Pre-order: root first, затем поддеревья."""
    out = [root_id]
    ch = conn.execute(
        "SELECT id FROM work_items WHERE parent_id = ? ORDER BY created_at",
        (root_id,),
    ).fetchall()
    for row in ch:
        out.extend(_pre_order_ids(conn, row["id"]))
    return out


TERMINAL = frozenset({"done", "cancelled", "archived"})


def cancel_work_item_subtree(
    sm: StateMachine,
    conn: sqlite3.Connection,
    root_id: str,
    *,
    actor_role: str = Role.CREATOR.value,
) -> tuple[int, str | None]:
    """
    Каскад creator_cancelled (post-order). Возвращает (count, error).
    """
    root = conn.execute(
        "SELECT id, status FROM work_items WHERE id = ?", (root_id,)
    ).fetchone()
    if not root:
        return 0, "work_item not found"
    if root["status"] in TERMINAL:
        return 0, "work_item already terminal"

    order = _post_order_ids(conn, root_id)
    # Предпроверка
    for wid in order:
        st = conn.execute(
            "SELECT status FROM work_items WHERE id = ?", (wid,)
        ).fetchone()
        if not st:
            return 0, f"missing {wid}"
        if st["status"] in TERMINAL:
            continue
        ok, _msg = sm.can_transition(wid, "creator_cancelled")
        if not ok:
            return 0, f"cannot cancel {wid} from {st['status']}"

    n = 0
    for wid in order:
        st = conn.execute(
            "SELECT status FROM work_items WHERE id = ?", (wid,)
        ).fetchone()
        if not st or st["status"] in TERMINAL:
            continue
        ok, _msg = sm.apply_transition(
            wid,
            "creator_cancelled",
            actor_role=actor_role,
        )
        if not ok:
            return n, f"apply failed for {wid}"
        n += 1
    return n, None


def archive_work_item_subtree(
    sm: StateMachine,
    conn: sqlite3.Connection,
    root_id: str,
    *,
    actor_role: str = Role.CREATOR.value,
) -> tuple[int, str | None]:
    """
    Только узлы в статусе done → archive_sweep. Post-order: дети, затем root.
    """
    root = conn.execute(
        "SELECT id, status FROM work_items WHERE id = ?", (root_id,)
    ).fetchone()
    if not root:
        return 0, "work_item not found"
    if root["status"] != "done":
        return 0, "work_item is not done"

    order = _post_order_ids(conn, root_id)
    n = 0
    for wid in order:
        st = conn.execute(
            "SELECT status FROM work_items WHERE id = ?", (wid,)
        ).fetchone()
        if not st or st["status"] != "done":
            continue
        ok, _msg = sm.can_transition(wid, "archive_sweep")
        if not ok:
            return n, f"cannot archive {wid}"
        ok2, _ = sm.apply_transition(
            wid,
            "archive_sweep",
            actor_role=actor_role,
        )
        if not ok2:
            return n, f"archive apply failed for {wid}"
        n += 1
    return n, None


def _delete_runs_for_work_item(conn: sqlite3.Connection, wi_id: str) -> None:
    runs = conn.execute(
        "SELECT id FROM runs WHERE work_item_id = ?", (wi_id,)
    ).fetchall()
    for r in runs:
        rid = r["id"]
        conn.execute("DELETE FROM run_steps WHERE run_id = ?", (rid,))
        conn.execute("DELETE FROM file_changes WHERE run_id = ?", (rid,))
        conn.execute("DELETE FROM review_checks WHERE run_id = ?", (rid,))
        conn.execute("DELETE FROM artifacts WHERE run_id = ?", (rid,))
        conn.execute("DELETE FROM runs WHERE id = ?", (rid,))
    conn.execute("DELETE FROM file_changes WHERE work_item_id = ?", (wi_id,))
    conn.execute("DELETE FROM artifacts WHERE work_item_id = ?", (wi_id,))


def delete_work_item_subtree(
    conn: sqlite3.Connection,
    logger: FactoryLogger,
    root_id: str,
) -> tuple[int, str | None]:
    """
    Удаляет root и потомков; только если все узлы в draft или cancelled
    и нет «чужих» статусов у детей.
    """
    root = conn.execute(
        "SELECT id, status, kind FROM work_items WHERE id = ?", (root_id,)
    ).fetchone()
    if not root:
        return 0, "work_item not found"
    if root["status"] not in ("draft", "cancelled"):
        return 0, "only draft or cancelled can be deleted"

    all_ids = _pre_order_ids(conn, root_id)
    for wid in all_ids:
        st = conn.execute(
            "SELECT status FROM work_items WHERE id = ?", (wid,)
        ).fetchone()
        if st["status"] not in ("draft", "cancelled"):
            return 0, f"child {wid} has status {st['status']}, cannot delete"

    logger.log(
        EventType.WORK_ITEM_DELETED,
        "work_item",
        root_id,
        f"delete requested: {len(all_ids)} work_item(s)",
        work_item_id=root_id,
        actor_role=Role.CREATOR.value,
        payload={"delete_count_expected": len(all_ids), "ids": all_ids},
        tags=["api", "delete"],
    )

    # Удаляем снизу вверх (дети до родителя)
    ordered = list(reversed(all_ids))
    deleted = 0
    for wid in ordered:
        conn.execute("DELETE FROM judge_verdicts WHERE work_item_id = ?", (wid,))
        conn.execute("DELETE FROM review_results WHERE work_item_id = ?", (wid,))
        _delete_runs_for_work_item(conn, wid)
        conn.execute("DELETE FROM file_locks WHERE work_item_id = ?", (wid,))
        conn.execute("DELETE FROM work_item_queue WHERE work_item_id = ?", (wid,))
        conn.execute("DELETE FROM work_item_files WHERE work_item_id = ?", (wid,))
        conn.execute("DELETE FROM comments WHERE work_item_id = ?", (wid,))
        conn.execute("DELETE FROM architect_comments WHERE work_item_id = ?", (wid,))
        conn.execute("DELETE FROM decisions WHERE work_item_id = ?", (wid,))
        conn.execute("DELETE FROM context_snapshots WHERE work_item_id = ?", (wid,))
        conn.execute(
            "UPDATE work_items SET origin_work_item_id = NULL WHERE origin_work_item_id = ?",
            (wid,),
        )
        conn.execute(
            "DELETE FROM work_item_links WHERE src_id = ? OR dst_id = ?",
            (wid, wid),
        )
        try:
            conn.execute(
                "DELETE FROM improvement_candidates WHERE vision_id = ?",
                (wid,),
            )
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "DELETE FROM event_log WHERE work_item_id = ? OR entity_id = ?",
            (wid, wid),
        )
        conn.execute("DELETE FROM work_items WHERE id = ?", (wid,))
        deleted += 1

    return deleted, None


def list_done_vision_roots_ready_to_archive(conn: sqlite3.Connection) -> list[str]:
    """Vision в done, ещё не archived — готовы к archive_sweep."""
    rows = conn.execute(
        """
        SELECT id FROM work_items
        WHERE kind = 'vision' AND status = 'done'
        ORDER BY created_at DESC
        """
    ).fetchall()
    return [r["id"] for r in rows]
