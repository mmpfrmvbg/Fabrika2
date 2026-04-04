"""GET-эндпоинты дашборда: списки, детали, дерево (read-only, те же запросы что и dashboard_api)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

# Дублируем нормализацию kind с dashboard_api (без циклического импорта).
_KIND_LABELS: dict[str, str] = {
    "vision": "Vision",
    "story": "Story",
    "epic": "Epic",
    "task": "Task",
    "atom": "Atom",
}
_KIND_ALIASES: dict[str, str] = {
    "initiative": "story",
    "atm_change": "atom",
}


def _normalize_kind(db_kind: str | None) -> tuple[str, str]:
    k = (db_kind or "").strip().lower()
    if k in _KIND_ALIASES:
        k = _KIND_ALIASES[k]
    if k not in _KIND_LABELS:
        k = "task"
    return k, _KIND_LABELS[k]


def _canonical_level(kind: str) -> int:
    order = ("vision", "story", "epic", "task", "atom")
    try:
        return order.index(kind)
    except ValueError:
        return 0


def _raw_db_kinds_for_filter(canon: str) -> tuple[str, ...]:
    """Канонический kind → набор значений kind в БД для WHERE."""
    m: dict[str, tuple[str, ...]] = {
        "vision": ("vision",),
        "epic": ("epic",),
        "story": ("story", "initiative"),
        "task": ("task",),
        "atom": ("atom", "atm_change"),
    }
    return m.get(canon, (canon,))


def _row_wi_public(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    raw_kind = d.get("kind")
    nk, lbl = _normalize_kind(raw_kind if isinstance(raw_kind, str) else None)
    d["kind"] = nk
    d["label"] = lbl
    d["level"] = _canonical_level(nk)
    return d


def api_tasks_list(
    conn: sqlite3.Connection,
    *,
    kind: str | None = None,
    status: str | None = None,
    parent_id: str | None = None,
) -> dict[str, Any]:
    """Все work_items: id, parent_id, kind (норм.), title, description, status, created_at, updated_at. ORDER BY created_at DESC."""
    wheres: list[str] = []
    params: list[Any] = []
    if kind:
        canon = kind.strip().lower()
        raw_vals = _raw_db_kinds_for_filter(canon)
        placeholders = ",".join("?" * len(raw_vals))
        wheres.append(f"LOWER(kind) IN ({placeholders})")
        params.extend([x.lower() for x in raw_vals])
    if status:
        wheres.append("status = ?")
        params.append(status.strip())
    if parent_id is not None:
        if parent_id == "":
            wheres.append("parent_id IS NULL")
        else:
            wheres.append("parent_id = ?")
            params.append(parent_id)
    where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
    rows = conn.execute(
        f"""
        SELECT id, parent_id, kind, title, description, status, created_at, updated_at
        FROM work_items
        {where_sql}
        ORDER BY created_at DESC
        """,
        params,
    ).fetchall()
    items = []
    for r in rows:
        pub = _row_wi_public(r)
        items.append(
            {
                "id": pub["id"],
                "parent_id": pub["parent_id"],
                "kind": pub["kind"],
                "title": pub["title"],
                "description": pub["description"],
                "status": pub["status"],
                "created_at": pub["created_at"],
                "updated_at": pub["updated_at"],
            }
        )
    return {"items": items, "count": len(items)}


def api_work_items_list(
    conn: sqlite3.Connection,
    *,
    kind: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """
    GET /api/work-items — список work_items с полями для дашборда + вложенные ``files``.
    """
    wheres: list[str] = []
    params: list[Any] = []
    if kind:
        canon = kind.strip().lower()
        raw_vals = _raw_db_kinds_for_filter(canon)
        placeholders = ",".join("?" * len(raw_vals))
        wheres.append(f"LOWER(kind) IN ({placeholders})")
        params.extend([x.lower() for x in raw_vals])
    if status:
        wheres.append("status = ?")
        params.append(status.strip())
    where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
    rows = conn.execute(
        f"""
        SELECT id, parent_id, root_id, kind, title, description, status,
               priority, owner_role, created_at, updated_at
        FROM work_items
        {where_sql}
        ORDER BY created_at DESC
        """,
        params,
    ).fetchall()
    ids = [r["id"] for r in rows]
    files_map: dict[str, list[dict[str, Any]]] = {}
    if ids:
        ph = ",".join("?" * len(ids))
        fr = conn.execute(
            f"""
            SELECT work_item_id, path, intent, description
            FROM work_item_files
            WHERE work_item_id IN ({ph})
            ORDER BY path ASC
            """,
            ids,
        ).fetchall()
        for f in fr:
            wid = f["work_item_id"]
            files_map.setdefault(wid, []).append(
                {
                    "path": f["path"],
                    "intent": f["intent"],
                    "description": (f["description"] or "") or "",
                }
            )
    items: list[dict[str, Any]] = []
    for r in rows:
        pub = _row_wi_public(r)
        wid = r["id"]
        items.append(
            {
                "id": wid,
                "parent_id": r["parent_id"],
                "root_id": r["root_id"],
                "kind": pub["kind"],
                "label": pub["label"],
                "title": r["title"],
                "description": r["description"],
                "status": r["status"],
                "priority": r["priority"],
                "owner_role": r["owner_role"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "files": files_map.get(wid, []),
            }
        )
    return {"items": items, "count": len(items)}


def get_work_items_paginated(
    conn: sqlite3.Connection,
    limit: int,
    offset: int,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Пагинированный список work_items c общим количеством."""
    filters = filters or {}
    wheres: list[str] = []
    params: list[Any] = []
    status = filters.get("status")
    parent_id = filters.get("parent_id")
    if status:
        wheres.append("status = ?")
        params.append(str(status))
    if parent_id is not None:
        if parent_id == "":
            wheres.append("parent_id IS NULL")
        else:
            wheres.append("parent_id = ?")
            params.append(parent_id)
    where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""

    total = int(
        conn.execute(
            f"SELECT COUNT(*) AS c FROM work_items{where_sql}",
            params,
        ).fetchone()["c"]
    )
    rows = conn.execute(
        f"""
        SELECT id, kind, parent_id, title, status, created_at
        FROM work_items
        {where_sql}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()
    items = [dict(r) for r in rows]
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(items)) < total,
    }


def api_task_detail(conn: sqlite3.Connection, wi_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM work_items WHERE id = ?", (wi_id,)).fetchone()
    if not row:
        return {"error": "not_found", "work_item": None}

    base = _row_wi_public(row)
    files_rows = conn.execute(
        """
        SELECT path, intent, description, required
        FROM work_item_files
        WHERE work_item_id = ?
        ORDER BY path
        """,
        (wi_id,),
    ).fetchall()
    base["files"] = [
        {
            "path": f["path"],
            "intent": f["intent"],
            "description": f["description"] or "",
            "required": bool(f["required"]),
        }
        for f in files_rows
    ]

    ch_rows = conn.execute(
        """
        SELECT * FROM work_items WHERE parent_id = ?
        ORDER BY created_at ASC
        """,
        (wi_id,),
    ).fetchall()
    children = [_row_wi_public(c) for c in ch_rows]

    from .dashboard_api import _runs_detail

    runs_detail = _runs_detail(conn, wi_id)
    runs = runs_detail.get("runs") or []

    fc_all: list[dict] = []
    for run in runs:
        rid = run["id"]
        for fc in run.get("file_changes") or []:
            x = dict(fc)
            x["run_id"] = rid
            fc_all.append(x)

    comments_out: list[dict] = []
    try:
        cm = conn.execute(
            """
            SELECT id, work_item_id, author_role, comment_type, body, created_at
            FROM comments
            WHERE work_item_id = ?
            ORDER BY created_at ASC
            """,
            (wi_id,),
        ).fetchall()
        for c in cm:
            comments_out.append(dict(c))
    except sqlite3.OperationalError:
        pass

    return {
        "work_item": base,
        "children": children,
        "runs": runs,
        "file_changes": fc_all,
        "comments": comments_out,
    }


def api_task_events_chronological(
    conn: sqlite3.Connection, wi_id: str, limit: int = 2000
) -> dict[str, Any]:
    """События по work_item / run (как в _events), порядок хронологический (старые первые)."""
    wheres = [
        "(work_item_id = ? OR (entity_type = 'work_item' AND entity_id = ?) "
        "OR run_id IN (SELECT id FROM runs WHERE work_item_id = ?))"
    ]
    params: list = [wi_id, wi_id, wi_id]
    where_sql = " WHERE " + " AND ".join(wheres)

    rows = conn.execute(
        f"""
        SELECT id, event_time, event_type, severity, message, entity_type, entity_id,
               actor_role, run_id, work_item_id, payload
        FROM event_log
        {where_sql}
        ORDER BY id ASC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    items = []
    for r in rows:
        msg = (r["message"] or "").replace("\n", " ")
        sev = (r["severity"] or "info").lower()
        if sev == "fatal":
            sev = "error"
        pl_raw = r["payload"]
        payload_obj = None
        if pl_raw:
            try:
                payload_obj = json.loads(pl_raw)
            except json.JSONDecodeError:
                payload_obj = None
        et = r["event_time"]
        items.append(
            {
                "id": r["id"],
                "event_time": et,
                "created_at": et,
                "event_type": r["event_type"],
                "severity": sev,
                "actor_role": r["actor_role"] or "—",
                "message": msg,
                "entity_type": r["entity_type"],
                "entity_id": r["entity_id"],
                "run_id": r["run_id"],
                "work_item_id": r["work_item_id"],
                "payload": payload_obj,
            }
        )
    return {"work_item_id": wi_id, "items": items, "count": len(items)}


def api_tree_nested(conn: sqlite3.Connection) -> dict[str, Any]:
    """Корни — work_items без parent_id; children рекурсивно вложены."""
    rows = conn.execute(
        """
        SELECT id, parent_id, root_id, kind, title, description, status,
               creator_role, owner_role, planning_depth, priority,
               created_at, updated_at
        FROM work_items
        ORDER BY created_at ASC
        """
    ).fetchall()

    nodes: dict[str, dict[str, Any]] = {}
    for r in rows:
        pub = _row_wi_public(r)
        pub["children"] = []
        nodes[pub["id"]] = pub

    roots: list[dict[str, Any]] = []
    for wid, node in nodes.items():
        pid = node.get("parent_id")
        if pid and pid in nodes:
            nodes[pid]["children"].append(node)
        elif not pid:
            roots.append(node)

    def sort_children(n: dict[str, Any]) -> None:
        ch = n.get("children") or []
        ch.sort(key=lambda x: (x.get("created_at") or "", x.get("id") or ""))
        for c in ch:
            sort_children(c)

    roots.sort(key=lambda x: (x.get("created_at") or "", x.get("id") or ""))
    for r0 in roots:
        sort_children(r0)

    return {"roots": roots}
