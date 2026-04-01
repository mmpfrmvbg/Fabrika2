"""
Каноническое представление ReviewResult — docs/PHASE2_AGENT_CONTRACT.md §9.
Ревьюер обязан вернуть STRICT JSON, совместимый с этой моделью.
"""

from __future__ import annotations

import json
from typing import Any, List, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

_PASS = "review_passed"
_FAIL = "review_failed"


class ReviewResultValidationError(ValueError):
    """Невалидный JSON или несоответствие схеме ReviewResult / FSM."""


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Literal["low", "medium", "high"]
    message: str


class ReviewResult(BaseModel):
    """Строгая структура результата ревью (межагентный контракт PHASE2)."""

    model_config = ConfigDict(extra="forbid")

    item: str
    run_id: str
    verdict: Literal["approved", "rejected"]
    checked_artifacts: List[str]
    all_passed: bool
    issues: List[ReviewIssue] = Field(default_factory=list)
    context_refs: List[str] = Field(default_factory=list)
    next_event: str

    @field_validator("run_id", mode="before")
    @classmethod
    def _coerce_run_id(cls, v: Any) -> str:
        if v is None:
            raise ValueError("run_id is required")
        return str(v)

    @model_validator(mode="after")
    def _verdict_consistency(self) -> ReviewResult:
        if self.verdict == "approved":
            if not self.all_passed:
                raise ValueError("approved requires all_passed=true")
            for i in self.issues:
                if i.severity == "high":
                    raise ValueError("approved cannot include high-severity issues")
        else:
            if self.all_passed:
                raise ValueError("rejected requires all_passed=false")
            if not self.issues:
                raise ValueError("rejected requires at least one issue")
        return self


def parse_review_result(raw: str) -> ReviewResult:
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ReviewResultValidationError(f"Review output is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ReviewResultValidationError("Review JSON must be an object")
    try:
        return ReviewResult.model_validate(data)
    except ValidationError as e:
        raise ReviewResultValidationError(str(e)) from e


def validate_review_fsm_alignment(*, result: ReviewResult) -> None:
    """Согласование verdict и next_event с FSM (только review_passed / review_failed)."""
    if result.verdict == "approved":
        if result.next_event != _PASS:
            raise ReviewResultValidationError(
                f'approved verdict requires next_event={_PASS!r}, got {result.next_event!r}'
            )
        return
    if result.next_event != _FAIL:
        raise ReviewResultValidationError(
            f'rejected verdict requires next_event={_FAIL!r}, got {result.next_event!r}'
        )


def validate_subject_run_alignment(
    *,
    result: ReviewResult,
    latest_implement_run_id: str | None,
) -> None:
    """
    Если в БД есть последний implement-run кузницы по work item, поле run_id в ReviewResult
    должно с ним совпадать. Иначе (сценарии без forge-run) проверка не применяется.
    """
    if latest_implement_run_id is None:
        return
    if result.run_id != latest_implement_run_id:
        raise ReviewResultValidationError(
            f"run_id must match latest implement run {latest_implement_run_id!r}, "
            f"got {result.run_id!r}"
        )
