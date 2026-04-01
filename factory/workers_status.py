"""Снимок активных lease в ``work_item_queue`` (worker-ы и оркестратор)."""

from __future__ import annotations

import sqlite3
from typing import Any


def workers_status_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT wiq.lease_owner, wiq.work_item_id, wiq.queue_name, wiq.lease_until,
               wi.status AS work_status, wi.title AS work_title
        FROM work_item_queue wiq
        LEFT JOIN work_items wi ON wi.id = wiq.work_item_id
        WHERE wiq.lease_owner IS NOT NULL
          AND wiq.lease_until IS NOT NULL
          AND wiq.lease_until > strftime('%Y-%m-%dT%H:%M:%f','now')
        ORDER BY wiq.lease_until DESC
        """
    ).fetchall()
    workers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        oid = (r["lease_owner"] or "").strip()
        if not oid or oid in seen:
            continue
        seen.add(oid)
        cur_atom = r["work_item_id"]
        workers.append(
            {
                "id": oid,
                "last_seen": r["lease_until"],
                "current_atom": cur_atom,
                "queue_name": r["queue_name"],
                "work_status": r["work_status"],
                "work_title": (r["work_title"] or "")[:120],
            }
        )
    return {
        "active": len(workers),
        "workers": workers,
        "leases_total": len(rows),
    }
