"""
Дерево work_items для GET /api/work-items/tree и GET /api/tree.

Иерархия по parent_id; корни — без родителя. Для атомов — последнее событие из event_log.
"""

from __future__ import annotations

import sqlite3
from typing import Any

_KIND_SHORT: dict[str, str] = {
    "vision": "VSN",
    "initiative": "INI",
    "epic": "EPC",
    "story": "STR",
    "task": "TSK",
    "atom": "ATM",
    "atm_change": "ATM",
}


def _atom_last_event_map(conn: sqlite3.Connection, atom_ids: list[str]) -> dict[str, str]:
    """event_type последней записи event_log по каждому atom id."""
    if not atom_ids:
        return {}
    ph = ",".join("?" * len(atom_ids))
    rows = conn.execute(
        f"""
        SELECT el.work_item_id, el.event_type
        FROM event_log el
        INNER JOIN (
            SELECT work_item_id, MAX(id) AS max_id
            FROM event_log
            WHERE work_item_id IN ({ph})
            GROUP BY work_item_id
        ) t ON el.id = t.max_id
        """,
        atom_ids,
    ).fetchall()
    return {r["work_item_id"]: (r["event_type"] or "") for r in rows}


def build_work_items_tree(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, parent_id, root_id, kind, title, status, created_at,
               owner_role, assigned_agent_id, description
        FROM work_items
        """
    ).fetchall()
    by_id: dict[str, dict[str, Any]] = {}
    children: dict[str | None, list[str]] = {}

    atom_ids: list[str] = []
    for r in rows:
        d = dict(r)
        by_id[d["id"]] = d
        if (d.get("kind") or "") in ("atom", "atm_change"):
            atom_ids.append(d["id"])
        pid = d.get("parent_id")
        pkey: str | None = None if pid in (None, "") else str(pid)
        children.setdefault(pkey, []).append(d["id"])

    last_ev = _atom_last_event_map(conn, atom_ids)

    def node_payload(wi: dict[str, Any]) -> dict[str, Any]:
        k = wi.get("kind") or "task"
        out: dict[str, Any] = {
            "id": wi["id"],
            "kind": k,
            "kind_short": _KIND_SHORT.get(k, k.upper()[:3]),
            "title": wi.get("title") or "",
            "status": wi.get("status") or "",
            "created_at": wi.get("created_at"),
            "owner_role": wi.get("owner_role"),
            "assignee": wi.get("owner_role"),
            "children": [],
        }
        if wi.get("description"):
            out["description"] = wi["description"]
        if wi.get("assigned_agent_id"):
            out["assigned_agent_id"] = wi["assigned_agent_id"]
        if k in ("atom", "atm_change"):
            le = last_ev.get(wi["id"])
            if le:
                out["last_event"] = le
        return out

    def nest(wi_id: str) -> dict[str, Any]:
        wi = by_id[wi_id]
        n = node_payload(wi)
        for cid in sorted(children.get(wi_id, []), key=lambda x: by_id[x].get("created_at") or ""):
            n["children"].append(nest(cid))
        return n

    roots_ids = children.get(None, []) + children.get("", [])
    # стабильный порядок
    roots_ids = sorted(set(roots_ids), key=lambda x: by_id[x].get("created_at") or "")
    return [nest(rid) for rid in roots_ids]


def subtree_for_root_id(conn: sqlite3.Connection, root_id: str) -> dict[str, Any] | None:
    """Один корень дерева (например Vision) — для ответа POST /api/visions."""
    for node in build_work_items_tree(conn):
        if node.get("id") == root_id:
            return node
    return None
