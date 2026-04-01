"""FSM: find_matching_transition, apply_transition (Фаза 1)."""
import json
import sqlite3
from typing import Optional

from .actions import Actions
from .db import transaction
from .guards import Guards
from .logging import FactoryLogger
from .models import EventType, Role, Severity, WorkItemStatus


class StateMachine:
    """Правила из state_transitions; переход в одной транзакции."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        guards: Guards,
        actions: Actions,
        logger: FactoryLogger,
    ):
        self.conn = conn
        self.guards = guards
        self.actions = actions
        self.logger = logger
        self._transitions: list[dict] = self._load_transitions()

    def _load_transitions(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM state_transitions").fetchall()
        return [dict(r) for r in rows]

    def reload(self):
        self._transitions = self._load_transitions()

    @staticmethod
    def _norm_guard(g) -> str:
        return (g or "").strip()

    def find_matching_transition(
        self, wi: sqlite3.Row | dict, event_name: str
    ) -> Optional[dict]:
        wid = wi["id"]
        w = dict(wi) if not isinstance(wi, dict) else wi
        candidates = []
        for rule in self._transitions:
            if rule["entity_type"] != "work_item":
                continue
            fs = rule["from_state"]
            if fs not in ("*", w["status"]):
                continue
            if rule["event_name"] != event_name:
                continue
            kinds_json = rule.get("applicable_kinds")
            if kinds_json:
                try:
                    allowed = (
                        json.loads(kinds_json)
                        if isinstance(kinds_json, str)
                        else kinds_json
                    )
                    if w["kind"] not in allowed:
                        continue
                except (json.JSONDecodeError, TypeError):
                    pass
            candidates.append(rule)

        candidates.sort(
            key=lambda r: (
                0 if self._norm_guard(r.get("guard_name")) else 1,
                r.get("id", ""),
            )
        )

        for rule in candidates:
            gn = self._norm_guard(rule.get("guard_name"))
            if gn:
                guard_fn = self.guards.resolve(gn)
                ok, _reason = guard_fn(wid)
                if ok:
                    return rule
            else:
                return rule
        return None

    def can_transition(self, wi_id: str, event_name: str) -> tuple[bool, str]:
        wi = self.conn.execute(
            "SELECT * FROM work_items WHERE id = ?", (wi_id,)
        ).fetchone()
        if not wi:
            return False, f"work_item {wi_id} не найден"

        rule = self.find_matching_transition(wi, event_name)
        if not rule:
            return False, f"Нет перехода для status={wi['status']}, event={event_name}"

        return True, rule["to_state"]

    def _resolve_owner(self, new_status: str, wi: sqlite3.Row) -> str:
        owner_map = {
            WorkItemStatus.PLANNED.value: Role.PLANNER.value,
            WorkItemStatus.READY_FOR_JUDGE.value: Role.JUDGE.value,
            WorkItemStatus.JUDGE_REJECTED.value: wi["creator_role"],
            WorkItemStatus.READY_FOR_WORK.value: Role.FORGE.value,
            WorkItemStatus.IN_PROGRESS.value: Role.FORGE.value,
            WorkItemStatus.IN_REVIEW.value: Role.REVIEWER.value,
            WorkItemStatus.REVIEW_REJECTED.value: Role.JUDGE.value,
            WorkItemStatus.DONE.value: wi["owner_role"],
        }
        return owner_map.get(new_status, wi["owner_role"])

    def apply_transition(
        self,
        wi_id: str,
        event_name: str,
        *,
        actor_role: str = None,
        actor_id: str = None,
        run_id: str = None,
        extra_context: dict = None,
    ) -> tuple[bool, str]:
        wi = self.conn.execute(
            "SELECT * FROM work_items WHERE id = ?", (wi_id,)
        ).fetchone()
        if not wi:
            return False, f"work_item {wi_id} не найден"

        old_status = wi["status"]
        rule = self.find_matching_transition(wi, event_name)
        if not rule:
            # Нет подходящего правила (в т.ч. guard не прошёл): не засоряем stderr уровнем WARN
            # при каждом тике оркестратора (например forge_inbox + file_lock).
            self.logger.log(
                EventType.TASK_STATUS_CHANGED,
                "work_item",
                wi_id,
                f"{event_name} not valid from {old_status}",
                severity=Severity.DEBUG,
                work_item_id=wi_id,
                payload={"denied": True, "event": event_name, "from_state": old_status},
            )
            return False, f"No valid transition for {event_name} from {old_status}"

        new_status = rule["to_state"]
        if isinstance(new_status, str) and new_status.startswith("{") and new_status.endswith("}"):
            field = new_status[1:-1]
            w = dict(wi)
            new_status = w.get(field) or "planned"

        new_owner = self._resolve_owner(new_status, wi)

        ctx = {
            "run_id": run_id,
            "old_status": old_status,
            "new_status": new_status,
            "event_name": event_name,
            "actor_role": actor_role,
            "actor_id": actor_id,
            **(extra_context or {}),
        }

        with transaction(self.conn):
            self.conn.execute(
                """
                UPDATE work_items
                SET status = ?, owner_role = ?, previous_status = ?
                WHERE id = ?
                """,
                (new_status, new_owner, old_status, wi_id),
            )

            self.logger.log(
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
                    "guard": self._norm_guard(rule.get("guard_name")),
                    "action": rule.get("action_name") or "",
                },
                tags=["fsm", event_name],
            )
            if event_name == "forge_started":
                self.logger.log(
                    EventType.FORGE_STARTED,
                    "work_item",
                    wi_id,
                    "Forge started",
                    work_item_id=wi_id,
                    run_id=run_id,
                    actor_role=actor_role,
                )
            elif event_name == "forge_completed":
                self.logger.log(
                    EventType.FORGE_COMPLETED,
                    "work_item",
                    wi_id,
                    "Forge completed",
                    work_item_id=wi_id,
                    run_id=run_id,
                    actor_role=actor_role,
                )
            elif event_name == "forge_failed":
                self.logger.log(
                    EventType.FORGE_FAILED,
                    "work_item",
                    wi_id,
                    "Forge failed",
                    work_item_id=wi_id,
                    run_id=run_id,
                    actor_role=actor_role,
                )

            aname = rule.get("action_name") or ""
            if aname:
                action_fn = self.actions.resolve(aname)
                action_fn(wi_id, **ctx)

        return True, f"{old_status} -> {new_status}"


def find_matching_transition(
    sm: StateMachine, wi: sqlite3.Row | dict, event_name: str
):
    """Канонический API Фазы 2: делегирует в `StateMachine.find_matching_transition`."""
    return sm.find_matching_transition(wi, event_name)


def apply_transition(
    sm: StateMachine,
    wi_id: str,
    event_name: str,
    *,
    actor_role: str = None,
    actor_id: str = None,
    run_id: str = None,
    extra_context: dict = None,
) -> tuple[bool, str]:
    """Канонический API Фазы 2: делегирует в `StateMachine.apply_transition`."""
    return sm.apply_transition(
        wi_id,
        event_name,
        actor_role=actor_role,
        actor_id=actor_id,
        run_id=run_id,
        extra_context=extra_context,
    )
