"""Read-only СЌРЅРґРїРѕРёРЅС‚С‹ РґР»СЏ В«Р¶РёРІРѕРіРѕВ» РґР°С€Р±РѕСЂРґР° (SQLite SELECT). Р‘РµР· РёРјРїРѕСЂС‚Р° РёР· dashboard_api (С†РёРєР»С‹)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .dashboard_api_read import _normalize_kind


def _canonical_level(kind: str) -> int:
    order = ("vision", "story", "epic", "task", "atom")
    try:
        return order.index(kind)
    except ValueError:
        return 0


def _last_event_per_work_item(conn: sqlite3.Connection) -> dict[str, dict]:
    try:
        rows = conn.execute(
            """
            SELECT e.work_item_id, e.id, e.event_type, e.event_time, e.message, e.payload
            FROM event_log e
            INNER JOIN (
                SELECT work_item_id, MAX(id) AS mid
                FROM event_log
                WHERE work_item_id IS NOT NULL
                GROUP BY work_item_id
            ) t ON e.work_item_id = t.work_item_id AND e.id = t.mid
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        wid = r["work_item_id"]
        pl_raw = r["payload"]
        payload_obj = None
        if pl_raw:
            try:
                payload_obj = json.loads(pl_raw)
            except json.JSONDecodeError:
                payload_obj = None
        msg = (r["message"] or "").replace("\n", " ")
        out[wid] = {
            "id": r["id"],
            "event_type": r["event_type"],
            "event_time": r["event_time"],
            "message": msg,
            "payload": payload_obj,
        }
    return out


def _atom_kinds_sql() -> str:
    return "LOWER(kind) IN ('atom','atm_change')"


def api_visions_with_atom_counts(conn: sqlite3.Connection) -> dict[str, Any]:
    """GET /api/visions вЂ” items + atoms_by_status РїРѕ root_id = vision.id."""
    rows = conn.execute(
        """
        SELECT id, title, description, status, created_at, updated_at
        FROM work_items
        WHERE LOWER(kind) = 'vision'
        ORDER BY datetime(created_at) DESC, id DESC
        """
    ).fetchall()
    items = []
    for r in rows:
        vid = r["id"]
        cnt_rows = conn.execute(
            f"""
            SELECT status, COUNT(*) AS c
            FROM work_items
            WHERE root_id = ? AND {_atom_kinds_sql()}
            GROUP BY status
            """,
            (vid,),
        ).fetchall()
        atoms_by_status = {row["status"]: int(row["c"]) for row in cnt_rows}
        items.append(
            {
                "id": vid,
                "title": r["title"],
                "description": r["description"],
                "status": r["status"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "atoms_by_status": atoms_by_status,
            }
        )
    return {"items": items}


def api_vision_tree(conn: sqlite3.Connection, vision_id: str) -> dict[str, Any]:
    """GET /api/visions/{id}/tree вЂ” РІР»РѕР¶РµРЅРЅРѕРµ РґРµСЂРµРІРѕ РїРѕРґ РєРѕСЂРЅРµРІС‹Рј Vision."""
    vid = vision_id.strip()
    root = conn.execute(
        "SELECT id, kind FROM work_items WHERE id = ?",
        (vid,),
    ).fetchone()
    if not root:
        return {"error": "not_found", "vision_id": vid}
    nk, _ = _normalize_kind(root["kind"] if isinstance(root["kind"], str) else None)
    if nk != "vision":
        return {"error": "not_a_vision", "vision_id": vid}

    rows = conn.execute(
        """
        SELECT id, parent_id, root_id, kind, title, description, status,
               planning_depth, created_at, updated_at
        FROM work_items
        WHERE root_id = ? OR id = ?
        ORDER BY planning_depth ASC, datetime(created_at) ASC, id ASC
        """,
        (vid, vid),
    ).fetchall()

    last_ev = _last_event_per_work_item(conn)
    atom_ids: list[str] = []
    for r in rows:
        k, _ = _normalize_kind(r["kind"] if isinstance(r["kind"], str) else None)
        if k == "atom":
            atom_ids.append(r["id"])

    files_by_wi: dict[str, list[dict]] = {}
    if atom_ids:
        ph = ",".join("?" * len(atom_ids))
        fr = conn.execute(
            f"""
            SELECT work_item_id, path, intent, description
            FROM work_item_files
            WHERE work_item_id IN ({ph})
            """,
            atom_ids,
        ).fetchall()
        for f in fr:
            wid = f["work_item_id"]
            files_by_wi.setdefault(wid, []).append(
                {
                    "path": f["path"],
                    "intent": f["intent"],
                    "description": f["description"] or "",
                }
            )

    nodes: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = dict(r)
        nk2, lbl = _normalize_kind(d.get("kind") if isinstance(d.get("kind"), str) else None)
        lev = _canonical_level(nk2)
        node: dict[str, Any] = {
            "id": d["id"],
            "parent_id": d["parent_id"],
            "title": d["title"],
            "status": d["status"],
            "level": lev,
            "kind": nk2,
            "label": lbl,
            "children": [],
        }
        if nk2 == "atom":
            node["assigned_files"] = files_by_wi.get(d["id"], [])
            node["last_event"] = last_ev.get(d["id"])
        nodes[d["id"]] = node

    roots: list[dict[str, Any]] = []
    for wid, node in nodes.items():
        pid = node.get("parent_id")
        if pid and pid in nodes:
            nodes[pid]["children"].append(node)
        elif wid == vid:
            roots.append(node)

    def sort_ch(n: dict[str, Any]) -> None:
        ch = n.get("children") or []
        ch.sort(key=lambda x: (x.get("title") or "", x.get("id") or ""))
        for c in ch:
            sort_ch(c)

    for r0 in roots:
        sort_ch(r0)

    return {"vision_id": vid, "roots": roots}


def api_work_item_subtree(conn: sqlite3.Connection, wi_id: str) -> dict[str, Any]:
    """
    GET /api/work-items/<id>/tree — иерархия от указанного узла вниз (все потомки).
    Формат как у ``api_vision_tree``: ``roots`` — один корень (сам узел) с ``children``.
    """
    root_id = wi_id.strip()
    root = conn.execute(
        "SELECT id, kind FROM work_items WHERE id = ?",
        (root_id,),
    ).fetchone()
    if not root:
        return {"error": "not_found", "work_item_id": root_id}

    rows = conn.execute(
        """
        WITH RECURSIVE sub(id) AS (
          SELECT ?
          UNION ALL
          SELECT w.id FROM work_items w JOIN sub ON w.parent_id = sub.id
        )
        SELECT wi.id, wi.parent_id, wi.root_id, wi.kind, wi.title, wi.description, wi.status,
               wi.planning_depth, wi.created_at, wi.updated_at
        FROM work_items wi
        WHERE wi.id IN (SELECT id FROM sub)
        ORDER BY wi.planning_depth ASC, datetime(wi.created_at) ASC, wi.id ASC
        """,
        (root_id,),
    ).fetchall()

    last_ev = _last_event_per_work_item(conn)
    atom_ids: list[str] = []
    for r in rows:
        k, _ = _normalize_kind(r["kind"] if isinstance(r["kind"], str) else None)
        if k == "atom":
            atom_ids.append(r["id"])

    files_by_wi: dict[str, list[dict]] = {}
    if atom_ids:
        ph = ",".join("?" * len(atom_ids))
        fr = conn.execute(
            f"""
            SELECT work_item_id, path, intent, description
            FROM work_item_files
            WHERE work_item_id IN ({ph})
            """,
            atom_ids,
        ).fetchall()
        for f in fr:
            wid = f["work_item_id"]
            files_by_wi.setdefault(wid, []).append(
                {
                    "path": f["path"],
                    "intent": f["intent"],
                    "description": f["description"] or "",
                }
            )

    nodes: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = dict(r)
        nk2, lbl = _normalize_kind(d.get("kind") if isinstance(d.get("kind"), str) else None)
        lev = _canonical_level(nk2)
        node: dict[str, Any] = {
            "id": d["id"],
            "parent_id": d["parent_id"],
            "title": d["title"],
            "status": d["status"],
            "level": lev,
            "kind": nk2,
            "label": lbl,
            "children": [],
        }
        if nk2 == "atom":
            node["assigned_files"] = files_by_wi.get(d["id"], [])
            node["last_event"] = last_ev.get(d["id"])
        nodes[d["id"]] = node

    roots: list[dict[str, Any]] = []
    for wid, node in nodes.items():
        pid = node.get("parent_id")
        if pid and pid in nodes:
            nodes[pid]["children"].append(node)
        elif wid == root_id:
            roots.append(node)

    def sort_ch(n: dict[str, Any]) -> None:
        ch = n.get("children") or []
        ch.sort(key=lambda x: (x.get("title") or "", x.get("id") or ""))
        for c in ch:
            sort_ch(c)

    for r0 in roots:
        sort_ch(r0)

    return {"root_id": root_id, "roots": roots}


def api_atoms_list(conn: sqlite3.Connection, status: str | None) -> dict[str, Any]:
    """GET /api/atoms вЂ” С„РёР»СЊС‚СЂ ?status= ; failed = forge_failed run last."""
    st = (status or "").strip().lower()
    wheres = [_atom_kinds_sql().replace("kind", "wi.kind")]
    params: list[Any] = []

    if st == "failed":
        wheres.append(
            """
            wi.id IN (
              SELECT DISTINCT r.work_item_id FROM runs r
              WHERE r.status = 'failed' AND r.role = 'forge'
            )
            """
        )
    elif st in (
        "ready_for_work",
        "in_progress",
        "done",
        "in_review",
        "draft",
        "planned",
    ):
        wheres.append("wi.status = ?")
        params.append(st)

    where_sql = " AND ".join(wheres)
    rows = conn.execute(
        f"""
        SELECT wi.id, wi.title, wi.status, wi.parent_id, wi.root_id,
               pt.title AS parent_task_title
        FROM work_items wi
        LEFT JOIN work_items pt ON pt.id = wi.parent_id
        WHERE {where_sql}
        ORDER BY datetime(wi.updated_at) DESC, wi.id DESC
        LIMIT 500
        """,
        params,
    ).fetchall()

    last_ev = _last_event_per_work_item(conn)
    items = []
    for r in rows:
        wid = r["id"]
        files = conn.execute(
            "SELECT path, intent FROM work_item_files WHERE work_item_id = ?",
            (wid,),
        ).fetchall()
        le = last_ev.get(wid)
        items.append(
            {
                "id": wid,
                "title": r["title"],
                "status": r["status"],
                "parent_task_title": r["parent_task_title"] or "",
                "files": [{"path": f["path"], "intent": f["intent"]} for f in files],
                "last_event_at": (le or {}).get("event_time"),
            }
        )
    return {"items": items, "count": len(items)}


def api_atom_log(conn: sqlite3.Connection, atom_id: str) -> dict[str, Any]:
    """GET /api/atoms/{id}/log вЂ” СЃРѕР±С‹С‚РёСЏ event_log РїРѕ work_item."""
    aid = atom_id.strip()
    row = conn.execute(
        "SELECT id, kind FROM work_items WHERE id = ?",
        (aid,),
    ).fetchone()
    if not row:
        return {"error": "not_found", "work_item_id": aid}
    nk, _ = _normalize_kind(row["kind"] if isinstance(row["kind"], str) else None)
    if nk != "atom":
        return {"error": "not_an_atom", "work_item_id": aid}

    rows = conn.execute(
        """
        SELECT event_time, event_type, actor_role, payload, message
        FROM event_log
        WHERE work_item_id = ?
        ORDER BY id ASC
        """,
        (aid,),
    ).fetchall()
    items = []
    for r in rows:
        pl = None
        if r["payload"]:
            try:
                pl = json.loads(r["payload"])
            except json.JSONDecodeError:
                pl = r["payload"]
        items.append(
            {
                "timestamp": r["event_time"],
                "event_type": r["event_type"],
                "actor_role": r["actor_role"] or "",
                "payload": pl,
                "message": (r["message"] or "")[:2000],
            }
        )
    return {"work_item_id": aid, "items": items}


def api_forge_inbox_simple(conn: sqlite3.Connection) -> dict[str, Any]:
    """GET /api/queue/forge_inbox вЂ” РѕС‡РµСЂРµРґСЊ forge_inbox."""
    rows = conn.execute(
        """
        SELECT wiq.work_item_id, wi.title, wiq.priority, wiq.created_at AS enqueued_at
        FROM work_item_queue wiq
        JOIN work_items wi ON wi.id = wiq.work_item_id
        WHERE wiq.queue_name = 'forge_inbox'
        ORDER BY wiq.priority ASC, wiq.available_at ASC, wiq.created_at ASC
        """
    ).fetchall()
    items = [
        {
            "work_item_id": r["work_item_id"],
            "title": r["title"],
            "priority": r["priority"],
            "enqueued_at": r["enqueued_at"],
        }
        for r in rows
    ]
    return {"items": items}


def api_stats_dashboard(conn: sqlite3.Connection) -> dict[str, Any]:
    """GET /api/stats вЂ” СЃРІРѕРґРєР° РїРѕ Р‘Р”."""
    total_visions = conn.execute(
        "SELECT COUNT(*) AS c FROM work_items WHERE LOWER(kind) = 'vision'"
    ).fetchone()["c"]
    total_atoms = conn.execute(
        f"SELECT COUNT(*) AS c FROM work_items WHERE {_atom_kinds_sql()}"
    ).fetchone()["c"]

    ab_rows = conn.execute(
        f"""
        SELECT status, COUNT(*) AS c FROM work_items
        WHERE {_atom_kinds_sql()}
        GROUP BY status
        """
    ).fetchall()
    atoms_by_status = {r["status"]: int(r["c"]) for r in ab_rows}

    total_forge_runs = conn.execute(
        """
        SELECT COUNT(*) AS c FROM runs
        WHERE role = 'forge' AND run_type = 'implement'
        """
    ).fetchone()["c"]

    last_row = conn.execute(
        """
        SELECT MAX(finished_at) AS mx FROM runs
        WHERE role = 'forge' AND run_type = 'implement' AND finished_at IS NOT NULL
        """
    ).fetchone()
    last_forge_run_at = last_row["mx"] if last_row else None

    done = conn.execute(
        """
        SELECT COUNT(*) AS c FROM runs
        WHERE role = 'forge' AND run_type = 'implement' AND status = 'completed'
        """
    ).fetchone()["c"]
    failed = conn.execute(
        """
        SELECT COUNT(*) AS c FROM runs
        WHERE role = 'forge' AND run_type = 'implement' AND status = 'failed'
        """
    ).fetchone()["c"]
    terminal = done + failed
    forge_success_rate = (done / terminal) if terminal else None

    wi_status_rows = conn.execute(
        """
        SELECT status, COUNT(*) AS c FROM work_items GROUP BY status
        """
    ).fetchall()
    work_items_by_status = {r["status"]: int(r["c"]) for r in wi_status_rows}

    total_runs_all = conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"]

    recent_ev = conn.execute(
        """
        SELECT id, event_time, event_type, message, work_item_id
        FROM event_log
        ORDER BY id DESC
        LIMIT 15
        """
    ).fetchall()
    recent_events = [
        {
            "id": r["id"],
            "event_time": r["event_time"],
            "event_type": r["event_type"],
            "message": (r["message"] or "").replace("\n", " ")[:500],
            "work_item_id": r["work_item_id"],
        }
        for r in recent_ev
    ]

    return {
        "total_visions": int(total_visions),
        "total_atoms": int(total_atoms),
        "atoms_by_status": atoms_by_status,
        "total_forge_runs": int(total_forge_runs),
        "last_forge_run_at": last_forge_run_at,
        "forge_success_rate": forge_success_rate,
        "work_items_by_status": work_items_by_status,
        "total_runs": int(total_runs_all),
        "recent_events": recent_events,
    }
