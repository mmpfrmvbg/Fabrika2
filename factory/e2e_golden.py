"""
Золотые e2e-сценарии для песочницы: временная БД, без ручных INSERT в прод.

- review_to_done: атом уже в in_review + review_inbox → один tick → done.
- chain: judge → forge-worker (FACTORY_QWEN_DRY_RUN) → reviewer → done.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .composition import wire
from .db import gen_id
from .models import QueueName, Role, WorkItemStatus


def _seed_review_only(conn, wi_id: str) -> None:
    conn.execute(
        """
        INSERT INTO work_items
            (id, root_id, kind, title, description, status, creator_role, owner_role, planning_depth)
        VALUES (?, ?, 'atom', 'E2E golden review', 'seed', ?, 'planner', 'reviewer', 0)
        """,
        (wi_id, wi_id, WorkItemStatus.IN_REVIEW.value),
    )
    conn.execute(
        """
        INSERT INTO work_item_queue (work_item_id, queue_name)
        VALUES (?, ?)
        """,
        (wi_id, QueueName.REVIEW_INBOX.value),
    )


def _seed_judge_forge_chain(conn, wi_id: str) -> None:
    conn.execute(
        """
        INSERT INTO work_items
            (id, root_id, kind, title, description, status, creator_role, owner_role, planning_depth)
        VALUES (?, ?, 'atom', 'E2E chain', 'seed', ?, 'planner', 'judge', 0)
        """,
        (wi_id, wi_id, WorkItemStatus.READY_FOR_JUDGE.value),
    )
    conn.execute(
        """
        INSERT INTO work_item_files (id, work_item_id, path, intent, required)
        VALUES (?, ?, ?, 'modify', 1)
        """,
        (gen_id("wif"), wi_id, "factory/__init__.py"),
    )
    conn.execute(
        """
        INSERT INTO work_item_queue (work_item_id, queue_name)
        VALUES (?, ?)
        """,
        (wi_id, QueueName.JUDGE_INBOX.value),
    )


def run_e2e_review_to_done() -> None:
    """Минимальный золотой путь: только ревьюер и review_passed → done."""
    path = Path(tempfile.mkstemp(prefix="factory_e2e_", suffix=".db")[1])
    f = None
    _prev = os.environ.get("FACTORY_QWEN_DRY_RUN")
    os.environ["FACTORY_QWEN_DRY_RUN"] = "1"
    try:
        f = wire(path)
        conn, orch = f["conn"], f["orchestrator"]
        wi_id = "e2e_atm_review"
        _seed_review_only(conn, wi_id)
        conn.commit()

        orch.tick()
        from factory.orchestrator_core import wait_for_async_workers

        wait_for_async_workers(timeout=60.0)

        row = conn.execute(
            "SELECT status FROM work_items WHERE id = ?",
            (wi_id,),
        ).fetchone()
        if not row or row["status"] != WorkItemStatus.DONE.value:
            raise RuntimeError(
                f"E2E review→done: ожидался status=done, получено {row and row['status']}"
            )
    finally:
        if _prev is None:
            os.environ.pop("FACTORY_QWEN_DRY_RUN", None)
        else:
            os.environ["FACTORY_QWEN_DRY_RUN"] = _prev
        if f is not None:
            f["conn"].close()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Windows: файл может оставаться заблокированным драйвером SQLite до сборки мусора
            pass


def run_e2e_chain_judge_forge_review_done() -> None:
    """
    Полная цепочка: forge-worker с ``FACTORY_QWEN_DRY_RUN=1`` (без реального Qwen CLI).
    """
    path = Path(tempfile.mkstemp(prefix="factory_e2e_", suffix=".db")[1])
    f = None
    _prev = os.environ.get("FACTORY_QWEN_DRY_RUN")
    os.environ["FACTORY_QWEN_DRY_RUN"] = "1"
    try:
        f = wire(path)
        conn, orch = f["conn"], f["orchestrator"]
        wi_id = "e2e_atm_chain"
        _seed_judge_forge_chain(conn, wi_id)
        conn.commit()

        orch.tick()  # judge → ready_for_work, forge_inbox
        from factory.orchestrator_core import wait_for_async_workers

        wait_for_async_workers(timeout=60.0)
        st = conn.execute(
            "SELECT status FROM work_items WHERE id = ?",
            (wi_id,),
        ).fetchone()["status"]
        if st != WorkItemStatus.READY_FOR_WORK.value:
            raise RuntimeError(f"После judge ожидался ready_for_work, получено {st}")

        orch.tick()  # forge_started + qwen dry + review → done
        wait_for_async_workers(timeout=60.0)

        row = conn.execute(
            "SELECT status FROM work_items WHERE id = ?",
            (wi_id,),
        ).fetchone()
        if not row or row["status"] != WorkItemStatus.DONE.value:
            raise RuntimeError(
                f"E2E chain: ожидался done, получено {row and row['status']}"
            )
    finally:
        if _prev is None:
            os.environ.pop("FACTORY_QWEN_DRY_RUN", None)
        else:
            os.environ["FACTORY_QWEN_DRY_RUN"] = _prev
        if f is not None:
            f["conn"].close()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
