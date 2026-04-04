"""Phase handler registry for FSM transition routing."""

from __future__ import annotations

from typing import Callable

from . import (
    phase_architect,
    phase_decompose,
    phase_forge,
    phase_judge,
    phase_planner,
    phase_review,
)

PHASE_REGISTRY = {
    "decompose": phase_decompose.handle,
    "architect": phase_architect.handle,
    "planner": phase_planner.handle,
    "forge": phase_forge.handle,
    "review": phase_review.handle,
    "judge": phase_judge.handle,
}
__all__ = [
    "PHASE_REGISTRY",
    "resolve_phase",
    "resolve_handler",
    "phase_decompose",
    "phase_architect",
    "phase_planner",
    "phase_forge",
    "phase_review",
    "phase_judge",
]


def resolve_phase(event_name: str) -> str:
    """Route events to a phase handler.

    The mapping is intentionally coarse-grained and only determines the module that
    executes the already selected transition rule.
    """
    if event_name in {
        "creator_submitted",
        "planner_assigned",
        "planner_decomposed",
        "parent_complete",
    }:
        return "decompose"
    if event_name in {
        "architect_submitted",
        "ready_for_review",
        "submitted_for_judgment",
    }:
        return "architect"
    if event_name in {
        "judge_approved_for_planning",
        "cancelled",
        "dependency_blocked",
        "dependency_resolved",
    }:
        return "planner"
    if event_name in {"forge_started", "forge_completed", "forge_failed"}:
        return "forge"
    if event_name in {"review_passed", "review_failed", "sent_to_judge"}:
        return "review"
    return "judge"


def resolve_handler(event_name: str) -> Callable[..., tuple[bool, str]]:
    """Return phase handler for the event."""
    return PHASE_REGISTRY[resolve_phase(event_name)]
