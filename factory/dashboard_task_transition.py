"""POST /api/tasks/<id>/transition — обёртка над FSM (event или to_status)."""

from __future__ import annotations

import json
import sqlite3

from .composition import wire
from .config import resolve_db_path
from .models import Role


def _resolve_event_for_to_status(
    conn: sqlite3.Connection, wi: sqlite3.Row, target_status: str
) -> tuple[str | None, str]:
    """Один однозначный event_name для перехода в to_state, иначе ошибка."""
    kind = wi["kind"]
    cur = wi["status"]
    rows = conn.execute(
        "SELECT from_state, to_state, event_name, applicable_kinds "
        "FROM state_transitions WHERE entity_type = ?",
        ("work_item",),
    ).fetchall()
    matches: list[str] = []
    for r in rows:
        fs = r["from_state"]
        if fs not in ("*", cur):
            continue
        ts = r["to_state"]
        if ts != target_status:
            continue
        if isinstance(ts, str) and ts.startswith("{") and ts.endswith("}"):
            continue
        ak = r["applicable_kinds"]
        if ak:
            try:
                allowed = json.loads(ak) if isinstance(ak, str) else ak
                if kind not in allowed:
                    continue
            except (json.JSONDecodeError, TypeError):
                continue
        matches.append(r["event_name"])
    # уникальные, стабильный порядок
    seen: set[str] = set()
    uniq = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            uniq.append(m)
    if len(uniq) == 1:
        return uniq[0], ""
    if not uniq:
        return None, f"no transition from {cur} to {target_status} for kind={kind}"
    return None, f"ambiguous transitions: {', '.join(uniq)}"


def post_task_transition(wi_id: str, body: dict) -> tuple[bool, dict, int]:
    if not isinstance(body, dict):
        return False, {"ok": False, "error": "JSON body required"}, 400

    event = body.get("event")
    if isinstance(event, str):
        event = event.strip()
    else:
        event = None

    to_status = body.get("to_status")
    if isinstance(to_status, str):
        to_status = to_status.strip()
    else:
        to_status = None

    db_path = resolve_db_path()
    factory = wire(db_path)
    conn = factory["conn"]
    sm = factory["sm"]
    try:
        wi = conn.execute("SELECT * FROM work_items WHERE id = ?", (wi_id,)).fetchone()
        if not wi:
            return False, {"ok": False, "error": "work_item not found"}, 404

        if not event and to_status:
            resolved, err = _resolve_event_for_to_status(conn, wi, to_status)
            if not resolved:
                return False, {"ok": False, "error": err}, 400
            event = resolved

        if not event:
            return (
                False,
                {"ok": False, "error": "provide event (FSM) or to_status (целевой статус)"},
                400,
            )

        ok, msg = sm.apply_transition(
            wi_id,
            event,
            actor_role=Role.CREATOR.value,
        )
        if not ok:
            return False, {"ok": False, "error": msg}, 400

        row = conn.execute(
            "SELECT status FROM work_items WHERE id = ?", (wi_id,)
        ).fetchone()
        st = row["status"] if row else None
        return True, {"ok": True, "status": st, "event": event}, 200
    finally:
        conn.close()
