"""Атомарный claim строки ``forge_inbox`` для внешних worker-процессов (без полного tick)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import sqlite3

from .models import QueueName


def claim_forge_inbox_atom(conn: sqlite3.Connection, agent_id: str) -> str | None:
    """
    Одна строка ``work_item_queue`` (forge_inbox + ready_for_work): lease как в ``Orchestrator._process_queue``.
    Возвращает ``work_item_id`` или ``None``.
    """
    if not (agent_id or "").strip():
        return None
    lease_until = (
        datetime.now(timezone.utc) + timedelta(minutes=30)
    ).isoformat()
    cur = conn.execute(
        """
        UPDATE work_item_queue
        SET lease_owner = ?, lease_until = ?
        WHERE rowid = (
            SELECT wiq.rowid FROM work_item_queue wiq
            INNER JOIN work_items wi ON wiq.work_item_id = wi.id
            WHERE wiq.queue_name = ?
              AND wiq.lease_owner IS NULL
              AND wiq.available_at <= strftime('%Y-%m-%dT%H:%M:%f','now')
              AND wiq.attempts < wiq.max_attempts
              AND wi.status = 'ready_for_work'
            ORDER BY wiq.priority ASC, wiq.created_at ASC
            LIMIT 1
        )
        RETURNING work_item_id
        """,
        (agent_id.strip(), lease_until, QueueName.FORGE_INBOX.value),
    )
    row = cur.fetchone()
    return str(row["work_item_id"]) if row else None


def release_queue_lease(conn: sqlite3.Connection, work_item_id: str) -> None:
    """Снимает lease (после ошибки до forge_started)."""
    conn.execute(
        """
        UPDATE work_item_queue
        SET lease_owner = NULL, lease_until = NULL
        WHERE work_item_id = ?
        """,
        (work_item_id,),
    )
