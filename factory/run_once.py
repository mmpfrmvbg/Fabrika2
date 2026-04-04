"""
Один проход оркестратора: взять первый атом из ``forge_inbox`` и довести forge-run до завершения.

Без бесконечного цикла: серия ``tick()`` до ``runs.status IN ('completed','failed')`` для целевого прогона.
"""

from __future__ import annotations

import os
import logging
from pathlib import Path

from .composition import wire
from .config import resolve_db_path
from .forge_next_atom import select_next_atom_for_forge

_LOG = logging.getLogger(__name__)


def run_run_once(db_path: Path | None = None) -> None:
    p = resolve_db_path(db_path)
    if not os.environ.get("FACTORY_WORKSPACE_ROOT"):
        os.environ["FACTORY_WORKSPACE_ROOT"] = str(p.parent)

    factory = wire(db_path)
    conn = factory["conn"]
    orch = factory["orchestrator"]

    picked = select_next_atom_for_forge(conn)
    if not picked:
        _LOG.info(
            "run-once: нет атомов в forge_inbox со статусом ready_for_work. "
            "Выполните: python -m factory --seed-demo"
        )
        return

    target = picked["id"]
    _LOG.info("run-once: целевой атом %s", target)

    max_ticks = int(os.environ.get("FACTORY_RUN_ONCE_MAX_TICKS", "400"))
    for i in range(max_ticks):
        orch.tick()
        run = conn.execute(
            """
            SELECT id, status FROM runs
            WHERE work_item_id = ? AND role = 'forge' AND run_type = 'implement'
            ORDER BY started_at DESC LIMIT 1
            """,
            (target,),
        ).fetchone()
        if run and run["status"] in ("completed", "failed"):
            _LOG.info(
                f"run-once: forge run {run['id']} finished ({run['status']}) after {i + 1} tick(s)"
            )
            return

    _LOG.warning(
        f"run-once: превышен лимит тиков ({max_ticks}); "
        "проверьте FACTORY_QWEN_DRY_RUN, очереди и логи."
    )
