"""Workers status helper — читает активные lease из work_item_queue."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from typing import Any


def workers_status_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    """Возвращает активные lease (внешние worker-процессы)."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        rows = conn.execute(
            """
            SELECT queue_name, lease_owner, lease_until, attempts, work_item_id
            FROM work_item_queue
            WHERE lease_owner IS NOT NULL AND lease_until > ?
            ORDER BY lease_until DESC
            """,
            (now_iso,),
        ).fetchall()
        workers = []
        for r in rows:
            workers.append({
                "queue": r["queue_name"],
                "owner": r["lease_owner"],
                "lease_until": r["lease_until"],
                "attempts": int(r["attempts"] or 0),
                "work_item_id": r["work_item_id"],
            })
        leases_total = conn.execute(
            "SELECT COUNT(*) AS c FROM work_item_queue WHERE lease_owner IS NOT NULL"
        ).fetchone()["c"]
        return {
            "active": len(workers),
            "workers": workers,
            "leases_total": int(leases_total),
        }
    except sqlite3.OperationalError:
        return {"active": 0, "workers": [], "leases_total": 0}
