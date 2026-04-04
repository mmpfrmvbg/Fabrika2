"""Shared transition execution logic used by phase handlers."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..db import transaction
from ..models import EventType
from ..webhooks import notify_state_change


def apply_rule(
    sm: Any,
    *,
    wi_id: str,
    wi: sqlite3.Row,
    rule: dict[str, Any],
    event_name: str,
    actor_role: str | None,
    actor_id: str | None,
    run_id: str | None,
    extra_context: dict[str, Any] | None,
) -> tuple[bool, str]:
    """Apply an already selected transition rule in a single transaction."""
    old_status = wi["status"]
    new_status = rule["to_state"]
    if (
        isinstance(new_status, str)
        and new_status.startswith("{")
        and new_status.endswith("}")
    ):
        field = new_status[1:-1]
        w = dict(wi)
        new_status = w.get(field) or "planned"

    new_owner = sm._resolve_owner(new_status, wi)

    ctx = {
        "run_id": run_id,
        "old_status": old_status,
        "new_status": new_status,
        "event_name": event_name,
        "actor_role": actor_role,
        "actor_id": actor_id,
        **(extra_context or {}),
    }

    with transaction(sm.conn):
        sm.conn.execute(
            """
            UPDATE work_items
            SET status = ?, owner_role = ?, previous_status = ?
            WHERE id = ?
            """,
            (new_status, new_owner, old_status, wi_id),
        )

        sm.logger.log(
            EventType.TASK_STATUS_CHANGED,
            "work_item",
            wi_id,
            f"{old_status} -> {new_status} via {event_name}",
            work_item_id=wi_id,
            run_id=run_id,
            actor_role=actor_role,
            actor_id=actor_id,
            payload={
                "from_state": old_status,
                "to_state": new_status,
                "event": event_name,
                "guard": sm._norm_guard(rule.get("guard_name")),
                "action": rule.get("action_name") or "",
            },
            tags=["fsm", event_name],
        )
        if event_name == "forge_started":
            sm.logger.log(
                EventType.FORGE_STARTED,
                "work_item",
                wi_id,
                "Forge started",
                work_item_id=wi_id,
                run_id=run_id,
                actor_role=actor_role,
            )
        elif event_name == "forge_completed":
            sm.logger.log(
                EventType.FORGE_COMPLETED,
                "work_item",
                wi_id,
                "Forge completed",
                work_item_id=wi_id,
                run_id=run_id,
                actor_role=actor_role,
            )
        elif event_name == "forge_failed":
            sm.logger.log(
                EventType.FORGE_FAILED,
                "work_item",
                wi_id,
                "Forge failed",
                work_item_id=wi_id,
                run_id=run_id,
                actor_role=actor_role,
            )

        notify_state_change(
            event_name=event_name,
            work_item_id=wi_id,
            title=wi["title"] if "title" in wi.keys() else None,
            status=new_status,
        )

        aname = rule.get("action_name") or ""
        if aname:
            action_names = [a.strip() for a in aname.split(";") if a.strip()]
            for action_name in action_names:
                action_fn = sm.actions.resolve(action_name)
                action_fn(wi_id, **ctx)

    return True, f"{old_status} -> {new_status}"


def handle_with_selected_rule(sm: Any, **kwargs: Any) -> tuple[bool, str]:
    """Default phase behavior: apply a selected transition rule."""
    return apply_rule(sm, **kwargs)
