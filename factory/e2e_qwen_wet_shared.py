"""
Общая подготовка wet-Qwen атома с ``factory/hello_qwen.py`` до ``ready_for_work`` (судья одобрил).

Используется ``--e2e-qwen-wet-edit``, ``--e2e-qwen-wet-failover``, ``--e2e-qwen-wet-forge-no-artifact`` —
один и тот же штатный путь FSM/оркестратора, без отдельной «спец-логики» для одного сценария.
"""

from __future__ import annotations

import sqlite3

from .models import CommentType, Role, WorkItemKind, WorkItemStatus
from .work_items import WorkItemOps

WET_EDIT_HELLO_PATH = "factory/hello_qwen.py"
WET_EDIT_ATOM_DESCRIPTION = (
    'В файле factory/hello_qwen.py реализуй функцию hello() так, чтобы она возвращала '
    'строку "Hello from Qwen" и ничего больше в проекте не трогай.'
)


def drive_wet_hello_atom_to_ready_for_work(
    conn: sqlite3.Connection,
    sm,
    orch,
    actions,
    ops: WorkItemOps,
) -> tuple[str, str]:
    """
    Vision → epic → atom (hello_qwen) → architect → judge → ``ready_for_work``.

    Возвращает ``(vision_id, atom_id)``. Вызывающий сам делает ``orch.tick()`` для forge.
    """
    vid = ops.create_vision(
        "Wet-edit E2E vision",
        "Один атом: правка hello_qwen.py под реальный Qwen",
        auto_commit=False,
    )
    ok, msg = sm.apply_transition(
        vid, "creator_submitted", actor_role=Role.CREATOR.value
    )
    if not ok:
        raise RuntimeError(f"creator_submitted: {msg}")
    conn.commit()

    orch.tick()

    epic_row = conn.execute(
        "SELECT id FROM work_items WHERE parent_id = ? ORDER BY created_at LIMIT 1",
        (vid,),
    ).fetchone()
    if not epic_row:
        raise RuntimeError("После planner нет дочернего epic")
    epic_id = epic_row["id"]

    orch.tick()

    atom_id = ops.create_child(
        epic_id,
        WorkItemKind.ATOM.value,
        "Wet Qwen: hello_qwen.py",
        WET_EDIT_ATOM_DESCRIPTION,
        files=[
            {
                "path": WET_EDIT_HELLO_PATH,
                "intent": "modify",
                "description": "single-file wet edit",
            }
        ],
        auto_commit=False,
    )
    ok, msg = sm.apply_transition(
        atom_id,
        "creator_submitted",
        actor_role=Role.PLANNER.value,
    )
    if not ok:
        raise RuntimeError(f"atom creator_submitted: {msg}")

    ops.add_comment(
        atom_id,
        Role.ARCHITECT.value,
        "OK",
        comment_type=CommentType.ANALYSIS.value,
        auto_commit=False,
    )
    conn.commit()

    ok, msg = sm.apply_transition(
        atom_id,
        "ready_for_review",
        actor_role=Role.PLANNER.value,
    )
    if not ok:
        raise RuntimeError(f"ready_for_review: {msg}")
    actions.action_notify_judge(atom_id)
    conn.commit()

    orch.tick()

    st = conn.execute(
        "SELECT status FROM work_items WHERE id = ?",
        (atom_id,),
    ).fetchone()["status"]
    if st != WorkItemStatus.READY_FOR_WORK.value:
        raise RuntimeError(f"Ожидался ready_for_work после judge, получено {st}")

    return vid, atom_id
