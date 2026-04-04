"""Журналирование в таблицу event_log + дублирование в stdlib logging."""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from .models import EventType, Severity
from .task_context import resolve_task_context


def _coerce_payload_dict(payload: dict[str, Any] | str | Any | None) -> dict[str, Any]:
    """payload в event_log всегда JSON-объект."""
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return dict(payload)
    if isinstance(payload, str):
        return {"message": payload}
    return {"value": payload}


class FactoryLogger:
    def __init__(self, conn: sqlite3.Connection | None) -> None:
        self.conn = conn
        self._py_logger = logging.getLogger("factory")

    def log(
        self,
        event_type: EventType,
        entity_type: str,
        entity_id: str,
        message: str,
        *,
        severity: Severity = Severity.INFO,
        run_id: str | None = None,
        work_item_id: str | None = None,
        actor_role: str | None = None,
        actor_id: str | None = None,
        account_id: str | None = None,
        caused_by_type: str | None = None,
        caused_by_id: str | None = None,
        parent_event_id: int | None = None,
        payload: dict[str, Any] | str | Any | None = None,
        tags: list[str] | None = None,
    ) -> int:
        if self.conn is None:
            sev = severity.value
            if sev == "warn":
                log_fn = self._py_logger.warning
            else:
                log_fn = getattr(self._py_logger, sev, self._py_logger.info)
            log_fn(f"[{event_type.value}] {entity_type}:{entity_id} — {message}")
            return 0

        ctx: dict = {}
        correlation_id: str | None = None
        if work_item_id:
            ctx = resolve_task_context(self.conn, work_item_id)
            crow = self.conn.execute(
                "SELECT correlation_id FROM work_items WHERE id = ?",
                (work_item_id,),
            ).fetchone()
            correlation_id = str(crow["correlation_id"]) if crow and crow["correlation_id"] else None
        elif run_id:
            row = self.conn.execute(
                "SELECT work_item_id, correlation_id FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row and row["correlation_id"]:
                correlation_id = str(row["correlation_id"])
            if row and row["work_item_id"]:
                ctx = resolve_task_context(self.conn, row["work_item_id"])
                if not correlation_id:
                    crow = self.conn.execute(
                        "SELECT correlation_id FROM work_items WHERE id = ?",
                        (row["work_item_id"],),
                    ).fetchone()
                    correlation_id = (
                        str(crow["correlation_id"]) if crow and crow["correlation_id"] else None
                    )
        if correlation_id:
            ctx["correlation_id"] = correlation_id
        merged_payload = {**_coerce_payload_dict(payload), **ctx}

        cursor = self.conn.execute(
            """
            INSERT INTO event_log
                (event_type, entity_type, entity_id, severity, message,
                 run_id, work_item_id, actor_role, actor_id, account_id, correlation_id,
                 caused_by_type, caused_by_id, parent_event_id, payload, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_type.value,
                entity_type,
                entity_id,
                severity.value,
                message,
                run_id,
                work_item_id,
                actor_role,
                actor_id,
                account_id,
                correlation_id,
                caused_by_type,
                caused_by_id,
                parent_event_id,
                json.dumps(merged_payload, ensure_ascii=False),
                json.dumps(tags, ensure_ascii=False) if tags else None,
            ),
        )
        event_id = cursor.lastrowid

        sev = severity.value
        if sev == "warn":
            log_fn = self._py_logger.warning
        else:
            log_fn = getattr(self._py_logger, sev, self._py_logger.info)
        log_fn(f"[{event_type.value}] {entity_type}:{entity_id} — {message}")

        return int(event_id or 0)
