"""Forge-worker: очередь прогонов `runs` (role=forge, status=queued) → ``forge_worker.execute_forge_run`` → FSM."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ..forge_worker import execute_forge_run
from ..models import Role, RunType

if TYPE_CHECKING:
    from ..orchestrator_core import Orchestrator


def run_forge_queued_runs(orchestrator: Orchestrator) -> None:
    """
    Берёт прогоны forge со статусом ``queued``, исполняет через ``execute_forge_run``,
    затем ``forge_completed`` или ``forge_failed``.

    Отключение: ``FACTORY_FORGE_USE_WORKER=0`` (старый путь с ручным ``forge_completed`` в тестах).
    """
    if os.environ.get("FACTORY_FORGE_USE_WORKER", "1").strip().lower() in (
        "0",
        "false",
        "no",
    ):
        return

    conn = orchestrator.conn
    sm = orchestrator.sm
    logger = orchestrator.logger
    accounts = orchestrator.accounts

    pending = conn.execute(
        """
        SELECT r.id AS run_id, r.work_item_id, r.account_id
        FROM runs r
        JOIN work_items wi ON wi.id = r.work_item_id
        WHERE r.role = ?
          AND r.run_type = ?
          AND r.status = 'queued'
          AND wi.status = 'in_progress'
        ORDER BY r.started_at ASC
        LIMIT 5
        """,
        (Role.FORGE.value, RunType.IMPLEMENT.value),
    ).fetchall()

    for row in pending:
        run_id = row["run_id"]
        wi_id = row["work_item_id"]

        conn.execute(
            "UPDATE runs SET status = 'running' WHERE id = ?",
            (run_id,),
        )

        execute_forge_run(conn, run_id, wi_id, accounts, logger, sm)
