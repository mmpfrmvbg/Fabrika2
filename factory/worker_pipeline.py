"""Один атом после ``forge_started``: review/judge без ``_dispatch_ready_atoms`` (для ``factory.worker``)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .agents import forge
from .models import QueueName, WorkItemStatus

if TYPE_CHECKING:
    pass


def drain_atom_downstream(orch: Any, wi_id: str, *, max_rounds: int = 60) -> None:
    """
    Повторяет шаги оркестратора, которые двигают пайплайн после forge (без новых forge dispatch).
    """
    conn = orch.conn
    terminal = frozenset(
        {
            WorkItemStatus.DONE.value,
            "cancelled",
            "archived",
            "blocked",
        }
    )
    for _ in range(max_rounds):
        orch._expire_leases()
        forge.run_forge_queued_runs(orch)
        orch.process_review_queue()
        orch._process_queue(QueueName.JUDGE_INBOX, orch._dispatch_judge)
        orch._escalate_review_rejected_to_judge()
        conn.commit()
        row = conn.execute(
            "SELECT status FROM work_items WHERE id = ?",
            (wi_id,),
        ).fetchone()
        if not row:
            return
        st = row["status"]
        if st in terminal:
            return
        # retry в кузницу — снаружи снова подхватит очередь
        if st == WorkItemStatus.READY_FOR_WORK.value:
            return
