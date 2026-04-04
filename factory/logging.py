"""Журналирование в таблицу event_log + дублирование в stdlib logging."""
import json
import logging
import sqlite3

from .models import EventType, Severity
from .task_context import resolve_task_context


def _coerce_payload_dict(payload: dict | None) -> dict:
    """payload в event_log всегда JSON-объект."""
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return dict(payload)
    if isinstance(payload, str):
        return {"message": payload}
    return {"value": payload}


class FactoryLogger:
    def __init__(self, conn: sqlite3.Connection):
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
        run_id: str = None,
        work_item_id: str = None,
        actor_role: str = None,
        actor_id: str = None,
        account_id: str = None,
        caused_by_type: str | None = None,
        caused_by_id: str | None = None,
        parent_event_id: int = None,
        payload: dict = None,
        tags: list = None,
    ) -> int:
        ctx: dict = {}
        if work_item_id:
            ctx = resolve_task_context(self.conn, work_item_id)
        elif run_id:
            row = self.conn.execute(
                "SELECT work_item_id FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row and row["work_item_id"]:
                ctx = resolve_task_context(self.conn, row["work_item_id"])
        merged_payload = {**_coerce_payload_dict(payload), **ctx}

        cursor = self.conn.execute(
            """
            INSERT INTO event_log
                (event_type, entity_type, entity_id, severity, message,
                 run_id, work_item_id, actor_role, actor_id, account_id,
                 caused_by_type, caused_by_id, parent_event_id, payload, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

        return event_id
