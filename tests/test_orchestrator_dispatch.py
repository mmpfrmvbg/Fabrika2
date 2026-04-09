from __future__ import annotations

from pathlib import Path

import pytest

from factory.composition import wire
from factory.models import QueueName, Role


def _insert_work_item_in_queue(conn, *, wi_id: str, queue: QueueName, status: str = "in_progress") -> None:
    now = "2026-04-01T10:00:00.000000Z"
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority,
            created_at, updated_at
        )
        VALUES (?, NULL, ?, 'task', ?, '', ?, 'creator', 'orchestrator', 0, 1, ?, ?)
        """,
        (wi_id, wi_id, wi_id, status, now, now),
    )
    conn.execute(
        """
        INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at, attempts, max_attempts)
        VALUES (?, ?, 10, ?, 0, 3)
        """,
        (wi_id, queue.value, now),
    )


def test_dispatch_judge_passes_orchestrator_and_item(monkeypatch, tmp_path: Path) -> None:
    f = wire(tmp_path / "dispatch_judge_ok.db")
    orch = f["orchestrator"]
    conn = f["conn"]
    captured: dict[str, object] = {}
    try:
        def _fake_run_judge(passed_orch, passed_item):
            captured["orch"] = passed_orch
            captured["item"] = passed_item

        monkeypatch.setattr("factory.orchestrator_core.judge.run_judge", _fake_run_judge)

        item = {"work_item_id": "wi_judge"}
        orch._dispatch_judge(item)

        assert captured == {"orch": orch, "item": item}
    finally:
        conn.close()


def test_dispatch_architect_passes_orchestrator_and_item(monkeypatch, tmp_path: Path) -> None:
    f = wire(tmp_path / "dispatch_arch_ok.db")
    orch = f["orchestrator"]
    conn = f["conn"]
    captured: dict[str, object] = {}
    try:
        def _fake_run_architect(passed_orch, passed_item):
            captured["orch"] = passed_orch
            captured["item"] = passed_item

        monkeypatch.setattr("factory.orchestrator_core.architect.run_architect", _fake_run_architect)

        item = {"work_item_id": "wi_architect"}
        orch._dispatch_architect(item)

        assert captured == {"orch": orch, "item": item}
    finally:
        conn.close()


def test_dispatch_reviewer_passes_orchestrator_and_item(monkeypatch, tmp_path: Path) -> None:
    f = wire(tmp_path / "dispatch_reviewer_ok.db")
    orch = f["orchestrator"]
    conn = f["conn"]
    captured: dict[str, object] = {}
    try:
        def _fake_run_review(passed_orch, passed_item):
            captured["orch"] = passed_orch
            captured["item"] = passed_item

        monkeypatch.setattr("factory.orchestrator_core.reviewer.run_review", _fake_run_review)

        item = {"work_item_id": "wi_reviewer"}
        orch._dispatch_reviewer(item)

        assert captured == {"orch": orch, "item": item}
    finally:
        conn.close()


def test_dispatch_completion_uses_parent_complete_and_orchestrator_role(tmp_path: Path) -> None:
    f = wire(tmp_path / "dispatch_completion_ok.db")
    orch = f["orchestrator"]
    conn = f["conn"]
    try:
        _insert_work_item_in_queue(conn, wi_id="wi_completion", queue=QueueName.COMPLETION_INBOX)
        conn.commit()

        captured: dict[str, object] = {}

        def _fake_apply_transition(work_item_id: str, action: str, *, actor_role: str):
            captured["work_item_id"] = work_item_id
            captured["action"] = action
            captured["actor_role"] = actor_role
            return True, "ok"

        orch.sm.apply_transition = _fake_apply_transition

        orch._dispatch_completion({"work_item_id": "wi_completion"})

        assert captured == {
            "work_item_id": "wi_completion",
            "action": "parent_complete",
            "actor_role": Role.ORCHESTRATOR.value,
        }
        queue_row = conn.execute(
            "SELECT 1 FROM work_item_queue WHERE work_item_id = ?",
            ("wi_completion",),
        ).fetchone()
        assert queue_row is None
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("queue_name", "dispatch_name", "patch_target"),
    [
        (QueueName.JUDGE_INBOX, "_dispatch_judge", "factory.orchestrator_core.judge.run_judge"),
        (QueueName.ARCHITECT_INBOX, "_dispatch_architect", "factory.orchestrator_core.architect.run_architect"),
        (QueueName.REVIEW_INBOX, "_dispatch_reviewer", "factory.orchestrator_core.reviewer.run_review"),
    ],
)
def test_dispatch_errors_are_handled_by_process_queue_for_external_dispatchers(
    monkeypatch,
    tmp_path: Path,
    queue_name: QueueName,
    dispatch_name: str,
    patch_target: str,
) -> None:
    f = wire(tmp_path / f"dispatch_err_{queue_name.value}.db")
    orch = f["orchestrator"]
    conn = f["conn"]
    try:
        wi_id = f"wi_{queue_name.value}"
        _insert_work_item_in_queue(conn, wi_id=wi_id, queue=queue_name)
        conn.commit()

        def _boom(_orch, _item):
            raise RuntimeError("dispatch exploded")

        monkeypatch.setattr(patch_target, _boom)

        orch._process_queue(queue_name, getattr(orch, dispatch_name))

        row = conn.execute(
            "SELECT attempts, last_error, lease_owner, lease_until FROM work_item_queue WHERE work_item_id = ?",
            (wi_id,),
        ).fetchone()
        assert row["attempts"] == 3
        assert "dispatch exploded" in (row["last_error"] or "")
        assert row["lease_owner"] is None
        assert row["lease_until"] is None
    finally:
        conn.close()


def test_dispatch_completion_error_is_handled_by_process_queue(tmp_path: Path) -> None:
    f = wire(tmp_path / "dispatch_completion_err.db")
    orch = f["orchestrator"]
    conn = f["conn"]
    try:
        wi_id = "wi_completion_err"
        _insert_work_item_in_queue(conn, wi_id=wi_id, queue=QueueName.COMPLETION_INBOX)
        conn.commit()

        def _boom_transition(_wi_id: str, _action: str, *, actor_role: str):
            assert actor_role == Role.ORCHESTRATOR.value
            raise RuntimeError("fsm exploded")

        orch.sm.apply_transition = _boom_transition

        orch._process_queue(QueueName.COMPLETION_INBOX, orch._dispatch_completion)

        row = conn.execute(
            "SELECT attempts, last_error, lease_owner, lease_until FROM work_item_queue WHERE work_item_id = ?",
            (wi_id,),
        ).fetchone()
        assert row["attempts"] == 3
        assert "fsm exploded" in (row["last_error"] or "")
        assert row["lease_owner"] is None
        assert row["lease_until"] is None
    finally:
        conn.close()
