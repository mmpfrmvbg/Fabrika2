"""Архитектор: комментарий analysis + проактивное сканирование (stub)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..db import gen_id
from ..models import CommentType, EventType, Role, RunType, StepKind
from ._helpers import finish_run, insert_run, insert_run_step, lease_queue_row

if TYPE_CHECKING:
    from ..orchestrator_core import Orchestrator


def run_architect(orchestrator: Orchestrator, item: dict) -> None:
    conn = orchestrator.conn
    logger = orchestrator.logger
    accounts = orchestrator.accounts

    wi_id = item["work_item_id"]
    account = accounts.get_active_account()
    run_id = gen_id("run")
    lease_queue_row(conn, wi_id, Role.ARCHITECT)
    insert_run(
        conn,
        run_id=run_id,
        wi_id=wi_id,
        role=Role.ARCHITECT,
        run_type=RunType.ANALYZE,
        account_id=account["account_id"],
        input_payload={"work_item_id": wi_id, "queue_item": dict(item)},
    )

    logger.log(
        EventType.RUN_STARTED,
        "run",
        run_id,
        "Run started (architect)",
        work_item_id=wi_id,
        run_id=run_id,
        caused_by_type="run",
        caused_by_id=run_id,
        actor_role=Role.ARCHITECT.value,
        account_id=account["account_id"],
    )

    structured = {
        "patterns": [],
        "affected_modules": [],
        "constraints": [],
        "risks": [],
        "priority_comment": "low",
    }
    conn.execute(
        """
        INSERT INTO comments
            (id, work_item_id, author_role, author_agent_id, comment_type, body, structured_payload)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            gen_id("cmt"),
            wi_id,
            Role.ARCHITECT.value,
            f"agent_{Role.ARCHITECT.value}",
            CommentType.ANALYSIS.value,
            "Архитектурный комментарий (stub Фаза 2).",
            json.dumps(structured, ensure_ascii=False),
        ),
    )

    insert_run_step(
        conn,
        run_id,
        1,
        StepKind.LLM_REPLY.value,
        {
            "step_kind": "llm_reply",
            "role": "architect",
            "model": "stub",
            "tokens_used": 0,
            "response_summary": "analysis comment recorded",
        },
    )

    logger.log(
        EventType.TASK_STATUS_CHANGED,
        "work_item",
        wi_id,
        "Архитектор оставил analysis",
        work_item_id=wi_id,
        run_id=run_id,
        actor_role=Role.ARCHITECT.value,
        tags=["architect", "phase2"],
        payload={"sub": "architect_commented"},
    )

    conn.execute(
        "DELETE FROM work_item_queue WHERE work_item_id = ?",
        (wi_id,),
    )
    finish_run(conn, run_id, ok=True, logger=logger)


def run_proactive_scan(orchestrator: Orchestrator) -> None:
    orchestrator.logger.log(
        EventType.TASK_STATUS_CHANGED,
        "system",
        "orchestrator",
        "Проактивное сканирование Архитектора (stub Фаза 2)",
        actor_role=Role.ARCHITECT.value,
        tags=["architect", "scan", "phase2"],
        payload={"sub": "architect_scan_started"},
    )
