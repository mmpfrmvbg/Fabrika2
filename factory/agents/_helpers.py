"""Общие вспомогательные функции для агентов (run, run_steps).

Атомарность: `StateMachine.apply_transition` фиксирует свою транзакцию внутри; прогон
(`runs` + `review_checks` + `run_steps`) и переход FSM — последовательные коммиты SQLite.
Сквозная атомарность «run + переход» без доработки FSM (SAVEPOINT / один внешний commit)
оставлена на последующие фазы — как у forge через `action_start_forge_run` в одном переходе.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from ..db import gen_id, payload_hash, stable_json_dumps
from ..models import EventType, Role, RunStatus, RunType, Severity

if TYPE_CHECKING:
    from ..logging import FactoryLogger


def lease_queue_row(conn: Any, wi_id: str, role: Role) -> str:
    agent_id = f"agent_{role.value}"
    lease_until = (
        datetime.now(timezone.utc) + timedelta(minutes=30)
    ).isoformat()
    conn.execute(
        """
        UPDATE work_item_queue
        SET lease_owner = ?, lease_until = ?
        WHERE work_item_id = ?
        """,
        (agent_id, lease_until, wi_id),
    )
    return agent_id


def insert_run(
    conn: Any,
    *,
    run_id: str,
    wi_id: str,
    role: Role,
    run_type: RunType,
    account_id: str,
    status: str = RunStatus.RUNNING.value,
    input_payload: Any = None,
    agent_version: str | None = None,
) -> None:
    agent_id = f"agent_{role.value}"
    agent_row = conn.execute(
        "SELECT model_name, prompt_version, config_json FROM agents WHERE id = ?",
        (agent_id,),
    ).fetchone()
    model_name_snapshot = agent_row["model_name"] if agent_row else None
    prompt_version = agent_row["prompt_version"] if agent_row else None
    model_params_json = agent_row["config_json"] if agent_row else None
    resolved_agent_version = agent_version or "unknown"
    wi_row = conn.execute(
        "SELECT correlation_id FROM work_items WHERE id = ?",
        (wi_id,),
    ).fetchone()
    correlation_id = wi_row["correlation_id"] if wi_row else None
    conn.execute(
        """
        INSERT INTO runs (
            id, work_item_id, agent_id, account_id, role, run_type, status, correlation_id,
            input_payload, input_hash, agent_version, prompt_version, model_name_snapshot, model_params_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            wi_id,
            agent_id,
            account_id,
            role.value,
            run_type.value,
            status,
            correlation_id,
            stable_json_dumps(input_payload) if input_payload is not None else None,
            payload_hash(input_payload) if input_payload is not None else None,
            resolved_agent_version,
            prompt_version,
            model_name_snapshot,
            model_params_json,
        ),
    )


def finish_run(
    conn: Any,
    run_id: str,
    *,
    ok: bool,
    error_summary: str | None = None,
    logger: FactoryLogger | None = None,
    failure_event: EventType | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    st = RunStatus.COMPLETED.value if ok else RunStatus.FAILED.value
    conn.execute(
        """
        UPDATE runs SET status = ?, finished_at = ?, error_summary = ?
        WHERE id = ?
        """,
        (st, now, error_summary, run_id),
    )
    if logger:
        row = conn.execute(
            "SELECT work_item_id, role FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row:
            wi = row["work_item_id"]
            if ok:
                et = EventType.RUN_COMPLETED
            else:
                et = failure_event or EventType.RUN_FAILED
            fail_sub: dict[str, str] = {}
            if not ok:
                if et == EventType.RUN_FAILED_ACCOUNT_EXHAUSTED:
                    fail_sub = {"sub": "account_exhausted"}
                elif et == EventType.RUN_FAILED_ACCOUNT_ROTATION_LIMIT:
                    fail_sub = {"sub": "account_rotation_limit"}
                elif et == EventType.RUN_FAILED_CLI_ERROR:
                    fail_sub = {"sub": "cli_error"}
                elif et == EventType.RUN_FAILED_FORGE_NO_ARTIFACT:
                    fail_sub = {"sub": "forge_no_artifact"}
                elif et == EventType.JUDGE_INVALID_OUTPUT:
                    fail_sub = {"sub": "judge_invalid_output"}
                elif et == EventType.REVIEW_INVALID_OUTPUT:
                    fail_sub = {"sub": "review_invalid_output"}
            logger.log(
                et,
                "run",
                run_id,
                "Run finished" if ok else (error_summary or "Run failed"),
                work_item_id=wi,
                run_id=run_id,
                caused_by_type="run",
                caused_by_id=run_id,
                actor_role=row["role"],
                severity=Severity.INFO if ok else Severity.WARN,
                payload={
                    "ok": ok,
                    "error_summary": error_summary,
                    **fail_sub,
                },
            )


def insert_run_step(
    conn: Any,
    run_id: str,
    step_no: int,
    step_kind: str,
    payload_obj: dict[str, Any],
    summary: str | None = None,
    agent_version: str | None = None,
) -> None:
    resolved_agent_version = agent_version
    if resolved_agent_version is None:
        row = conn.execute("SELECT agent_version FROM runs WHERE id = ?", (run_id,)).fetchone()
        resolved_agent_version = row["agent_version"] if row else None
    conn.execute(
        """
        INSERT OR IGNORE INTO run_steps (
            id, run_id, step_no, step_kind, status, summary, payload, input_hash, agent_version
        )
        VALUES (?, ?, ?, ?, 'completed', ?, ?, ?, ?)
        """,
        (
            gen_id("rs"),
            run_id,
            step_no,
            step_kind,
            summary or "",
            json.dumps(payload_obj, ensure_ascii=False),
            payload_hash(payload_obj),
            resolved_agent_version,
        ),
    )


def env_force_review_reject() -> bool:
    return os.environ.get("FACTORY_REVIEW_FORCE_REJECT", "").strip() in (
        "1",
        "true",
        "yes",
    )
