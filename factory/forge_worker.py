"""
Один forge-прогон: песочница → промпт → ``run_qwen_cli`` → захват diff → ``file_changes`` / ``run_steps`` → FSM.

Вызывается из ``agents.forge.run_forge_queued_runs`` после коммита перехода ``forge_started``.

По ``ForgeResult``: ``ok`` → ``forge_completed``; иначе см. ``EventType`` в ``finish_run``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from .db import payload_hash
from .forge_prompt import build_forge_prompt
from .forge_sandbox import (
    apply_sandbox_to_workspace,
    apply_dry_run_placeholder,
    capture_changes,
    cleanup_sandbox,
    declared_modify_paths_without_capture,
    persist_captured_changes,
    prepare_sandbox,
    resolve_effective_work_item_files,
    workspace_root,
)
from .models import EventType, Role, Severity, StepKind
from .qwen_cli_runner import _env_qwen_dry_run, run_qwen_cli
from .agents._helpers import finish_run, insert_run_step

if TYPE_CHECKING:
    from .config import AccountManager
    from .fsm import StateMachine
    from .logging import FactoryLogger


def _log_forge_run_result(
    logger: "FactoryLogger",
    work_item_id: str,
    run_id: str,
    *,
    success: bool,
    reason: str | None = None,
) -> None:
    logger.log(
        EventType.FORGE_RUN_RESULT,
        "work_item",
        work_item_id,
        f"forge_run_result: {'success' if success else 'failure'}",
        work_item_id=work_item_id,
        run_id=run_id,
        payload={"success": success, "reason": (reason or "").strip()},
        tags=["forge", "audit"],
    )


def execute_forge_run(
    conn: sqlite3.Connection,
    run_id: str,
    work_item_id: str,
    account_manager: AccountManager,
    logger: FactoryLogger,
    sm: StateMachine,
) -> None:
    """
    Читает атом, собирает промпт (``forge_prompt``), готовит песочницу (``forge_sandbox``),
    вызывает Qwen в ``cwd=sandbox``, пишет шаги и изменения файлов.
    """
    wi = conn.execute(
        "SELECT id, title, description FROM work_items WHERE id = ?",
        (work_item_id,),
    ).fetchone()
    if not wi:
        finish_run(
            conn,
            run_id,
            ok=False,
            error_summary="work_item missing",
            logger=logger,
        )
        _log_forge_run_result(
            logger, work_item_id, run_id, success=False, reason="work_item missing"
        )
        sm.apply_transition(
            work_item_id,
            "forge_failed",
            actor_role=Role.FORGE.value,
            run_id=run_id,
        )
        return

    repo_root = workspace_root()
    effective_files = resolve_effective_work_item_files(
        conn, work_item_id, repo_root, logger=logger, run_id=run_id
    )
    forge_body = build_forge_prompt(
        conn, work_item_id, repo_root=repo_root, effective_files=effective_files
    )
    forge_input_hash = payload_hash(
        {
            "work_item_id": work_item_id,
            "run_type": "implement",
            "prompt": forge_body,
        }
    )
    conn.execute("UPDATE runs SET input_hash = ? WHERE id = ?", (forge_input_hash, run_id))
    cached = conn.execute(
        """
        SELECT id
        FROM runs
        WHERE id != ?
          AND work_item_id = ?
          AND role = 'forge'
          AND run_type = 'implement'
          AND status = 'completed'
          AND input_hash = ?
        ORDER BY finished_at DESC, id DESC
        LIMIT 1
        """,
        (run_id, work_item_id, forge_input_hash),
    ).fetchone()
    if cached:
        logger.log(
            EventType.FORGE_STEP,
            "work_item",
            work_item_id,
            f"forge cache hit from run {cached['id']}",
            work_item_id=work_item_id,
            run_id=run_id,
            payload={"step": "idempotency_cache_hit", "cached_run_id": cached["id"]},
            tags=["forge", "cache"],
        )
        finish_run(conn, run_id, ok=True, logger=logger)
        ok_t, msg_t = sm.apply_transition(
            work_item_id,
            "forge_completed",
            actor_role=Role.FORGE.value,
            run_id=run_id,
        )
        if not ok_t:
            _log_forge_run_result(
                logger,
                work_item_id,
                run_id,
                success=False,
                reason=f"forge_completed denied: {msg_t}",
            )
            finish_run(
                conn,
                run_id,
                ok=False,
                error_summary=f"forge_completed denied: {msg_t}",
                logger=logger,
            )
            sm.apply_transition(
                work_item_id,
                "forge_failed",
                actor_role=Role.FORGE.value,
                run_id=run_id,
            )
            return
        _log_forge_run_result(logger, work_item_id, run_id, success=True)
        return

    ctx = None
    try:
        ctx = prepare_sandbox(
            conn, work_item_id, run_id, repo_root=repo_root, effective_files=effective_files
        )

        insert_run_step(
            conn,
            run_id,
            1,
            StepKind.PROMPT.value,
            {
                "step_kind": "prompt",
                "text": forge_body[:12000],
            },
            summary="forge_prompt",
        )

        full_len = len(forge_body or "")
        logger.log(
            EventType.FORGE_PROMPT_SENT,
            "work_item",
            work_item_id,
            f"forge_prompt_sent len={full_len}",
            work_item_id=work_item_id,
            run_id=run_id,
            payload={
                "preview_500": (forge_body or "")[:500],
                "full_length": full_len,
            },
            tags=["forge", "audit"],
        )

        fr = run_qwen_cli(
            conn=conn,
            account_manager=account_manager,
            logger=logger,
            work_item_id=work_item_id,
            run_id=run_id,
            title=wi["title"],
            description=wi["description"] or "",
            system_prompt=None,
            full_prompt=forge_body,
            cwd=str(ctx.root),
        )

        insert_run_step(
            conn,
            run_id,
            2,
            StepKind.LLM_REPLY.value,
            {
                "step_kind": "llm_reply",
                "ok": fr.ok,
                "stdout_preview": (fr.stdout or "")[:6000],
                "stderr_preview": (fr.stderr or "")[:6000],
                "exit_code": fr.exit_code,
            },
            summary="qwen_cli_reply",
        )

        insert_run_step(
            conn,
            run_id,
            3,
            StepKind.TOOL_RESULT.value,
            {
                "step_kind": "tool_result",
                "tool": "qwen_code_cli",
                "ok": fr.ok,
                "exhausted_accounts": fr.exhausted_accounts,
                "max_tries_reached": fr.max_tries_reached,
                "exit_code": fr.exit_code,
                "accounts_tried": fr.accounts_tried,
                "stdout_preview": (fr.stdout or "")[:2000],
                "stderr_preview": (fr.stderr or "")[:2000],
                "error_message": fr.error_message,
            },
            summary="qwen_cli_runner",
        )

        if fr.ok and _env_qwen_dry_run():
            apply_dry_run_placeholder(
                ctx, conn, work_item_id, effective_files=effective_files
            )

        if fr.ok:
            changes = capture_changes(ctx)
            if not _env_qwen_dry_run():
                # Wet mode: изменения должны реально попасть в workspace (иначе «код написан» только в песочнице).
                apply_sandbox_to_workspace(ctx=ctx, changes=changes, repo_root=repo_root)
                missing_modify = declared_modify_paths_without_capture(
                    conn, work_item_id, changes, effective_files=effective_files
                )
                if missing_modify:
                    insert_run_step(
                        conn,
                        run_id,
                        4,
                        StepKind.ERROR.value,
                        {
                            "step_kind": "forge_validation",
                            "reason": "no_artifact_for_declared_modify_paths",
                            "missing_paths": missing_modify,
                        },
                        summary="forge_no_artifact",
                    )
                    finish_run(
                        conn,
                        run_id,
                        ok=False,
                        error_summary=(
                            "Wet forge: нет изменений по объявленным modify-файлам: "
                            + ", ".join(missing_modify)
                        ),
                        logger=logger,
                        failure_event=EventType.RUN_FAILED_FORGE_NO_ARTIFACT,
                    )
                    ok_f, msg_f = sm.apply_transition(
                        work_item_id,
                        "forge_failed",
                        actor_role=Role.FORGE.value,
                        run_id=run_id,
                    )
                    if not ok_f:
                        logger.log(
                            EventType.FORGE_FAILED,
                            "work_item",
                            work_item_id,
                            f"forge_failed denied: {msg_f}",
                            severity=Severity.ERROR,
                            work_item_id=work_item_id,
                            run_id=run_id,
                            payload={"sub": "forge_failed_denied", "msg": msg_f},
                        )
                    _log_forge_run_result(
                        logger,
                        work_item_id,
                        run_id,
                        success=False,
                        reason="no_artifact_for_declared_modify_paths",
                    )
                    return

            persist_captured_changes(conn, work_item_id, run_id, changes)
            for ch in changes:
                logger.log(
                    EventType.FORGE_FILE_CHANGED,
                    "work_item",
                    work_item_id,
                    f"forge_file_changed {ch.path}",
                    run_id=run_id,
                    work_item_id=work_item_id,
                    payload={
                        "path": ch.path,
                        "change_type": ch.change_type,
                        "old_hash": ch.old_hash,
                        "new_hash": ch.new_hash,
                    },
                    tags=["forge", "file"],
                )
            base_step = 4
            for i, ch in enumerate(changes):
                insert_run_step(
                    conn,
                    run_id,
                    base_step + i,
                    StepKind.FILE_WRITE.value,
                    {
                        "step_kind": "file_write",
                        "path": ch.path,
                        "change_type": ch.change_type,
                        "old_hash": ch.old_hash,
                        "new_hash": ch.new_hash,
                    },
                    summary=ch.path,
                )

            logger.log(
                EventType.FORGE_SUCCEEDED,
                "work_item",
                work_item_id,
                "forge.succeeded",
                run_id=run_id,
                work_item_id=work_item_id,
                payload={"run_id": run_id},
                tags=["forge"],
            )
            finish_run(conn, run_id, ok=True, logger=logger)
            ok_t, msg_t = sm.apply_transition(
                work_item_id,
                "forge_completed",
                actor_role=Role.FORGE.value,
                run_id=run_id,
            )
            if not ok_t:
                logger.log(
                    EventType.FORGE_FAILED,
                    "work_item",
                    work_item_id,
                    f"forge_completed denied: {msg_t}",
                    severity=Severity.ERROR,
                    work_item_id=work_item_id,
                    run_id=run_id,
                    payload={"sub": "forge_completed_denied", "msg": msg_t},
                )
                _log_forge_run_result(
                    logger,
                    work_item_id,
                    run_id,
                    success=False,
                    reason=f"forge_completed denied: {msg_t}",
                )
            else:
                _log_forge_run_result(logger, work_item_id, run_id, success=True)
            return

        if fr.exhausted_accounts:
            finish_run(
                conn,
                run_id,
                ok=False,
                error_summary=fr.error_message or "all accounts exhausted",
                logger=logger,
                failure_event=EventType.RUN_FAILED_ACCOUNT_EXHAUSTED,
            )
        elif fr.max_tries_reached:
            finish_run(
                conn,
                run_id,
                ok=False,
                error_summary=fr.error_message
                or "account rotation limit reached without success",
                logger=logger,
                failure_event=EventType.RUN_FAILED_ACCOUNT_ROTATION_LIMIT,
            )
        else:
            finish_run(
                conn,
                run_id,
                ok=False,
                error_summary=fr.error_message
                or (fr.stderr or "")[:500]
                or "forge failed",
                logger=logger,
                failure_event=EventType.RUN_FAILED_CLI_ERROR,
            )

        fail_reason = (
            fr.error_message
            or (fr.stderr or "")[:500]
            or "forge failed"
            if not fr.exhausted_accounts and not fr.max_tries_reached
            else (
                fr.error_message
                or (
                    "all accounts exhausted"
                    if fr.exhausted_accounts
                    else "account rotation limit"
                )
            )
        )
        ok_f, msg_f = sm.apply_transition(
            work_item_id,
            "forge_failed",
            actor_role=Role.FORGE.value,
            run_id=run_id,
        )
        if not ok_f:
            logger.log(
                EventType.FORGE_FAILED,
                "work_item",
                work_item_id,
                f"forge_failed denied: {msg_f}",
                severity=Severity.ERROR,
                work_item_id=work_item_id,
                run_id=run_id,
                payload={"sub": "forge_failed_denied", "msg": msg_f},
            )
            fail_reason = f"{fail_reason}; forge_failed denied: {msg_f}"
        _log_forge_run_result(
            logger, work_item_id, run_id, success=False, reason=fail_reason
        )
    finally:
        cleanup_sandbox(ctx)


def run_forge_worker_loop(
    db_path: Path | None = None,
    *,
    idle_interval: float = 5.0,
) -> None:
    """
    Минимальный цикл: ``_dispatch_ready_atoms`` + ``run_forge_queued_runs`` (как в ``Orchestrator.tick``),
    без остальных очередей. Остановка: Ctrl+C.
    """
    import os
    import signal
    import time

    from .agents import forge
    from .composition import wire
    from .config import load_dotenv, resolve_db_path

    load_dotenv()
    p = resolve_db_path(db_path)
    if not os.environ.get("FACTORY_WORKSPACE_ROOT"):
        os.environ["FACTORY_WORKSPACE_ROOT"] = str(p.parent)

    factory = wire(db_path)
    conn = factory["conn"]
    orch = factory["orchestrator"]

    stop = False

    def _shutdown(_sig=None, _frame=None) -> None:
        nonlocal stop
        stop = True
        print("\nforge-worker: остановка (Ctrl+C)...")

    signal.signal(signal.SIGINT, _shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _shutdown)

    dry = os.environ.get("FACTORY_QWEN_DRY_RUN", "")
    print(
        f"forge-worker: DB={p} interval={idle_interval}s FACTORY_QWEN_DRY_RUN={dry!r} "
        f"(Ctrl+C to stop)"
    )

    try:
        while not stop:
            orch.accounts.get_active_account()

            pending_dispatch = conn.execute(
                """
                SELECT wiq.work_item_id, wi.title
                FROM work_item_queue wiq
                JOIN work_items wi ON wi.id = wiq.work_item_id
                WHERE wiq.queue_name = 'forge_inbox'
                  AND wi.status = 'ready_for_work'
                  AND wiq.lease_owner IS NULL
                  AND wiq.available_at <= strftime('%Y-%m-%dT%H:%M:%f','now')
                  AND wiq.attempts < wiq.max_attempts
                ORDER BY wiq.priority ASC, wiq.available_at ASC, wiq.created_at ASC
                LIMIT 1
                """
            ).fetchone()

            pending_run = conn.execute(
                """
                SELECT r.id, r.work_item_id
                FROM runs r
                JOIN work_items wi ON wi.id = r.work_item_id
                WHERE r.role = 'forge' AND r.run_type = 'implement'
                  AND r.status = 'queued' AND wi.status = 'in_progress'
                ORDER BY r.started_at ASC
                LIMIT 1
                """
            ).fetchone()

            if not pending_dispatch and not pending_run:
                print("Очередь пуста, жду...")
                conn.commit()
                time.sleep(idle_interval)
                continue

            target_wi = None
            if pending_dispatch:
                target_wi = pending_dispatch["work_item_id"]
                print(
                    f"Взял атом {pending_dispatch['work_item_id']} "
                    f"({pending_dispatch['title']}) — dispatch + forge..."
                )
            elif pending_run:
                target_wi = pending_run["work_item_id"]
                print(
                    f"Продолжаю forge-run {pending_run['id']} "
                    f"для атома {pending_run['work_item_id']}..."
                )

            orch._dispatch_ready_atoms()
            forge.run_forge_queued_runs(orch)
            conn.commit()

            if target_wi:
                run = conn.execute(
                    """
                    SELECT id, status FROM runs
                    WHERE work_item_id = ? AND role = 'forge' AND run_type = 'implement'
                    ORDER BY started_at DESC LIMIT 1
                    """,
                    (target_wi,),
                ).fetchone()
                if run and run["status"] in ("completed", "failed"):
                    ok = run["status"] == "completed"
                    print(
                        f"Forge завершён: {'success' if ok else 'fail'} "
                        f"(run {run['id']} -> {run['status']})"
                    )

            time.sleep(idle_interval)
    except KeyboardInterrupt:
        _shutdown()
    finally:
        try:
            conn.close()
        except Exception:
            pass
        print("forge-worker: выход.")


if __name__ == "__main__":
    import argparse
    import os
    from pathlib import Path

    from .config import load_dotenv

    ap = argparse.ArgumentParser(description="Forge worker: forge_inbox → dispatch → run_forge_queued_runs")
    ap.add_argument("--db", metavar="PATH", help="Путь к SQLite (или FACTORY_DB_PATH)")
    ap.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Пауза между циклами, сек (по умолчанию 5)",
    )
    args = ap.parse_args()
    if args.db:
        os.environ["FACTORY_DB_PATH"] = str(Path(args.db).resolve())
    load_dotenv()
    run_forge_worker_loop(idle_interval=args.interval)
