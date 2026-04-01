"""
Каноническое представление JudgeVerdict — совпадает с docs/PHASE2_AGENT_CONTRACT.md §8.
Судья обязан вернуть STRICT JSON, совместимый с этой моделью.
"""

from __future__ import annotations

import json
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..models import WorkItemKind

# События FSM для одобрения по kind (см. PHASE2 §1).
_APPROVE_FOR_ATOM = "judge_approved"
_APPROVE_FOR_PLANNING = "judge_approved_for_planning"
_REJECT = "judge_rejected"

_KINDS_ATOM = frozenset({WorkItemKind.ATOM.value, WorkItemKind.ATM_CHANGE.value})
_KINDS_PLANNING = frozenset(
    {
        WorkItemKind.VISION.value,
        WorkItemKind.INITIATIVE.value,
        WorkItemKind.EPIC.value,
        WorkItemKind.STORY.value,
        WorkItemKind.TASK.value,
    }
)


class JudgeVerdictValidationError(ValueError):
    """Невалидный JSON или несоответствие схеме JudgeVerdict / FSM."""


class JudgeVerdict(BaseModel):
    """Строгая структура вердикта судьи (межагентный контракт PHASE2)."""

    model_config = ConfigDict(extra="forbid")

    item: str
    verdict: Literal["approved", "rejected"]
    checked_guards: List[str]
    all_passed: bool
    context_refs: List[str] = Field(default_factory=list)
    next_event: str

    failed_guards: Optional[List[str]] = None
    rejection_reason_code: Optional[str] = None
    suggested_action: Optional[str] = None
    # Наблюдаемость: судья отметил использование окна unified journal по work_item (см. PHASE2 §10).
    used_event_log: bool = False

    @model_validator(mode="after")
    def _rejected_fields(self) -> JudgeVerdict:
        if self.verdict == "rejected":
            if not self.failed_guards:
                raise ValueError("rejected verdict requires non-empty failed_guards")
            if not (self.rejection_reason_code and self.rejection_reason_code.strip()):
                raise ValueError("rejected verdict requires rejection_reason_code")
        return self


def parse_judge_verdict(raw: str) -> JudgeVerdict:
    """Парсит raw-ответ судьи (строка) как JSON и валидирует JudgeVerdict."""
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as e:
        raise JudgeVerdictValidationError(f"Judge output is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise JudgeVerdictValidationError("Judge JSON must be an object")
    try:
        return JudgeVerdict.model_validate(data)
    except ValidationError as e:
        raise JudgeVerdictValidationError(str(e)) from e


def validate_verdict_fsm_alignment(*, work_item_kind: str, verdict: JudgeVerdict) -> None:
    """
    Проверяет согласованность verdict / all_passed / next_event с kind work item и FSM.
    Для rejected ожидается next_event judge_rejected; для approved — событие по kind.
    """
    if verdict.verdict == "approved":
        if not verdict.all_passed:
            raise JudgeVerdictValidationError(
                "approved verdict requires all_passed=true"
            )
        if work_item_kind in _KINDS_ATOM:
            expected = _APPROVE_FOR_ATOM
        elif work_item_kind in _KINDS_PLANNING:
            expected = _APPROVE_FOR_PLANNING
        else:
            raise JudgeVerdictValidationError(f"unknown work_item kind: {work_item_kind!r}")
        if verdict.next_event != expected:
            raise JudgeVerdictValidationError(
                f"next_event for approved {work_item_kind!r} must be {expected!r}, "
                f"got {verdict.next_event!r}"
            )
        return

    # rejected
    if verdict.all_passed:
        raise JudgeVerdictValidationError("rejected verdict requires all_passed=false")
    if verdict.next_event != _REJECT:
        raise JudgeVerdictValidationError(
            f"next_event for rejected must be {_REJECT!r}, got {verdict.next_event!r}"
        )
