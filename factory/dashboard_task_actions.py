"""Подсказки для дашборда: дочерний kind и допустимые переходы FSM."""

from __future__ import annotations

from typing import Any

from .composition import wire
from .dashboard_task_children import _expected_child_kind
from .dashboard_api_read import _normalize_kind

# События, которые имеет смысл предлагать Создателю с дашборда (проверка через can_transition).
_DASHBOARD_FSM_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("creator_submitted", "Активировать (draft → planned)"),
    ("ready_for_review", "Готово к ревью (нужен комментарий architect)"),
    ("submitted_for_judgment", "На судью (planned → ready_for_judge)"),
    ("judge_approved", "Одобрить (судья → дальше по виду задачи)"),
    ("author_revised", "Повторно на судью после правок"),
)


def enrich_task_detail(detail: dict[str, Any]) -> None:
    """Дополняет ответ GET /api/tasks/<id> полем ``dashboard`` (mutate)."""
    wi = detail.get("work_item")
    if not wi or not isinstance(wi, dict) or not wi.get("id"):
        return
    wid = wi["id"]
    pk, _ = _normalize_kind(wi.get("kind") if isinstance(wi.get("kind"), str) else None)
    child_kind = _expected_child_kind(pk)

    factory = wire()
    try:
        sm = factory["sm"]
        transitions = []
        for event_name, label in _DASHBOARD_FSM_CANDIDATES:
            ok, _msg = sm.can_transition(wid, event_name)
            if ok:
                transitions.append({"event": event_name, "label": label})
        detail["dashboard"] = {
            "child_kind": child_kind,
            "transitions": transitions,
        }
    finally:
        factory["conn"].close()
