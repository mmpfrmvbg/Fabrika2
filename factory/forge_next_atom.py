"""
Контракт «атом → forge_inbox» и выбор следующего атома для кузницы.

«Готов к кузнице» = ``WorkItemStatus.READY_FOR_WORK`` + очередь ``QueueName.FORGE_INBOX``
(как после ``judge_approved`` / ``action_enqueue_forge``), не отдельный статус в БД.

В работе кузницы: ``in_progress``, owner forge.
Успех кузницы (шаг реализации): ``forge_completed`` → ``in_review`` → (review) → ``done``.
Провал прогона: ``forge_failed`` → ``ready_for_work`` (retry) или ``ready_for_judge``.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from .fsm import StateMachine
from .models import QueueName, Role, RunType, WorkItemKind, WorkItemStatus
from .guards import Guards

if TYPE_CHECKING:
    from .orchestrator_core import Orchestrator

# Временный lease для остальных forge_inbox, пока крутится --run-next-atom (один целевой атом).
_RUN_NEXT_ATOM_HOLD_LEASE = "run_next_atom_hold"


def mark_atom_ready_for_forge(
    conn: sqlite3.Connection,
    sm: StateMachine,
    atom_id: str,
    *,
    orchestrator: Optional[Orchestrator] = None,
) -> None:
    """
    Переводит atom (или atm_change) из ``draft`` в ``ready_for_work`` и ставит в ``forge_inbox``.

    Путь: ``architect_submitted`` → судья (``judge_approved``). Если передан ``orchestrator``,
    вызывается ``agents.judge.run_judge`` — тот же прогон, что в E2E (run в ``runs`` с role=judge).
    Иначе — прямой ``sm.apply_transition(..., judge_approved)`` (без judge-run; только для узких тестов).

    Идемпотентно: если уже ``ready_for_work`` и очередь ``forge_inbox``, ничего не делает.
    """
    wi = conn.execute("SELECT * FROM work_items WHERE id = ?", (atom_id,)).fetchone()
    if not wi:
        raise ValueError(f"work_item не найден: {atom_id}")
    kind = (wi["kind"] or "").lower()
    if kind not in (WorkItemKind.ATOM.value, WorkItemKind.ATM_CHANGE.value):
        raise ValueError(f"ожидался atom/atm_change, kind={wi['kind']!r}")

    fc = conn.execute(
        "SELECT COUNT(*) AS c FROM work_item_files WHERE work_item_id = ?",
        (atom_id,),
    ).fetchone()["c"]
    if fc < 1:
        raise ValueError("у атома нет work_item_files — нельзя отправить в кузницу")

    st = wi["status"]
    if st == WorkItemStatus.READY_FOR_WORK.value:
        q = conn.execute(
            """
            SELECT 1 FROM work_item_queue
            WHERE work_item_id = ? AND queue_name = ?
            """,
            (atom_id, QueueName.FORGE_INBOX.value),
        ).fetchone()
        if q:
            return
        raise ValueError(
            f"атом уже ready_for_work, но не в {QueueName.FORGE_INBOX.value} — состояние неконсистентно"
        )

    if st != WorkItemStatus.DRAFT.value:
        raise ValueError(f"mark_atom_ready_for_forge: ожидался draft, status={st!r}")
    _g = Guards(conn)
    _ok, _reason = _g.guard_ready_for_forge(atom_id)
    if not _ok:
        raise ValueError(f"mark_atom_ready_for_forge: guard_ready_for_forge failed: {_reason}")


    ok, msg = sm.apply_transition(
        atom_id,
        "architect_submitted",
        actor_role=Role.ARCHITECT.value,
    )
    if not ok:
        raise RuntimeError(f"architect_submitted: {msg}")
    if orchestrator is not None:
        from .agents import judge

        judge.run_judge(orchestrator, {"work_item_id": atom_id})
    else:
        ok, msg = sm.apply_transition(
            atom_id,
            "judge_approved",
            actor_role=Role.JUDGE.value,
        )
        if not ok:
            raise RuntimeError(f"judge_approved: {msg}")

    # Enqueue into forge_inbox so select_next_atom_for_forge can find it
    from .models import QueueName
    conn.execute(
        """
        INSERT OR IGNORE INTO work_item_queue
            (work_item_id, queue_name, priority, available_at, attempts)
        VALUES (?, ?, 10, strftime('%Y-%m-%dT%H:%M:%f', 'now'), 0)
        """,
        (atom_id, QueueName.FORGE_INBOX.value),
    )
    conn.commit()

def select_next_atom_for_forge(conn: sqlite3.Connection) -> Optional[dict[str, Any]]:
    """
    Один atom / atm_change из forge_inbox, готовый к диспетчеризации (как в ``Orchestrator._dispatch_ready_atoms``).

    Порядок: ``work_items.priority`` ASC (меньше — раньше), ``created_at`` ASC, ``id`` ASC.
    Возвращает dict полей work_item + ключ ``files`` (список dict по work_item_files).
    """
    row = conn.execute(
        """
        SELECT wi.*
        FROM work_items wi
        JOIN work_item_queue wiq ON wiq.work_item_id = wi.id
        WHERE wiq.queue_name = ?
          AND wiq.lease_owner IS NULL
          AND wiq.available_at <= strftime('%Y-%m-%dT%H:%M:%f','now')
          AND wiq.attempts < wiq.max_attempts
          AND wi.status = ?
          AND wi.kind IN (?, ?)
        ORDER BY wi.priority ASC, wi.created_at ASC, wi.id ASC
        LIMIT 1
        """,
        (
            QueueName.FORGE_INBOX.value,
            WorkItemStatus.READY_FOR_WORK.value,
            WorkItemKind.ATOM.value,
            WorkItemKind.ATM_CHANGE.value,
        ),
    ).fetchone()
    if not row:
        return None
    out = dict(row)
    files = conn.execute(
        """
        SELECT id, path, intent, description
        FROM work_item_files
        WHERE work_item_id = ?
        ORDER BY path ASC
        """,
        (out["id"],),
    ).fetchall()
    out["files"] = [dict(f) for f in files]
    return out


def run_ticks_until_atom_done(
    conn: sqlite3.Connection,
    orch,
    atom_id: str,
    *,
    max_ticks: int | None = None,
) -> tuple[str, str | None]:
    """
    Серия ``orch.tick()`` до ``done`` по атому или исчерпания лимита.

    Возвращает ``(итоговый status атома, status последнего forge implement run или None)``.
    """
    limit = max_ticks if max_ticks is not None else int(
        os.environ.get("FACTORY_RUN_NEXT_ATOM_MAX_TICKS", "400")
    )
    for _ in range(limit):
        orch.tick()
        st = conn.execute(
            "SELECT status FROM work_items WHERE id = ?",
            (atom_id,),
        ).fetchone()
        if not st:
            break
        if st["status"] == WorkItemStatus.DONE.value:
            break
    final_wi = conn.execute(
        "SELECT status FROM work_items WHERE id = ?",
        (atom_id,),
    ).fetchone()
    atom_status = final_wi["status"] if final_wi else "missing"
    run_row = conn.execute(
        """
        SELECT status FROM runs
        WHERE work_item_id = ? AND role = ? AND run_type = ?
        ORDER BY started_at DESC
        LIMIT 1
        """,
        (atom_id, Role.FORGE.value, RunType.IMPLEMENT.value),
    ).fetchone()
    run_status = run_row["status"] if run_row else None
    return atom_status, run_status


def execute_run_next_atom(factory: dict) -> tuple[str | None, str | None, str | None]:
    """
    Берёт следующий атом из БД и гоняет оркестратор до ``done`` (или лимита тиков).

    Остальные строки ``forge_inbox`` временно получают lease, чтобы в тех же тиках
    не стартовали чужие атомы (поведение «один выбранный atom за вызов»).

    Возвращает ``(atom_id | None, final_atom_status | None, final_forge_run_status | None)``.
    """
    conn = factory["conn"]
    orch = factory["orchestrator"]
    picked = select_next_atom_for_forge(conn)
    if not picked:
        return None, None, None
    atom_id = picked["id"]
    hold_until = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    conn.execute(
        """
        UPDATE work_item_queue
        SET lease_owner = ?, lease_until = ?
        WHERE queue_name = ?
          AND work_item_id != ?
          AND lease_owner IS NULL
        """,
        (
            _RUN_NEXT_ATOM_HOLD_LEASE,
            hold_until,
            QueueName.FORGE_INBOX.value,
            atom_id,
        ),
    )
    conn.commit()
    try:
        atom_st, run_st = run_ticks_until_atom_done(conn, orch, atom_id)
        return atom_id, atom_st, run_st
    finally:
        conn.execute(
            """
            UPDATE work_item_queue
            SET lease_owner = NULL, lease_until = NULL
            WHERE lease_owner = ?
            """,
            (_RUN_NEXT_ATOM_HOLD_LEASE,),
        )
        conn.commit()
