from __future__ import annotations

from factory.models import _EDITABLE_STATUSES


def test_editable_statuses_exposed_from_models() -> None:
    assert _EDITABLE_STATUSES == frozenset(
        {"draft", "planned", "ready_for_judge", "judge_rejected"}
    )
