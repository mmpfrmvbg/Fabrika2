"""Ревьюер: review_checks + STRICT JSON ``ReviewResult`` → FSM ``review_passed`` / ``review_failed``."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import AccountManager
from ..contracts.review import (
    ReviewResult,
    ReviewResultValidationError,
    parse_review_result,
    validate_review_fsm_alignment,
    validate_subject_run_alignment,
)
from ..db import gen_id
from ..models import (
    CheckType,
    DecisionVerdict,
    EventType,
    Role,
    RunType,
    Severity,
    StepKind,
)
from ..qwen_cli_runner import run_qwen_cli
from ._helpers import (
    env_force_review_reject,
    finish_run,
    insert_run,
    insert_run_step,
    lease_queue_row,
)

if TYPE_CHECKING:
    from ..orchestrator_core import Orchestrator


def _item_ref(wi_kind: str, wi_id: str) -> str:
    return f"{wi_kind}:{wi_id}"


def _latest_implement_run_id(conn, wi_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT id FROM runs
        WHERE work_item_id = ? AND role = ? AND run_type = ?
        ORDER BY started_at DESC
        LIMIT 1
        """,
        (wi_id, Role.FORGE.value, RunType.IMPLEMENT.value),
    ).fetchone()
    return row["id"] if row else None


def _stub_review_raw_output(
    wi: dict,
    *,
    review_run_id: str,
    subject_run_id: str,
    blocking_failed: bool,
) -> str:
    """Заглушка «выхода модели»: корректный JSON ReviewResult."""
    kind = wi["kind"]
    wi_id = wi["id"]
    item = _item_ref(kind, wi_id)
    if blocking_failed:
        payload = {
            "item": item,
            "run_id": subject_run_id,
            "verdict": "rejected",
            "checked_artifacts": ["file_changes", "run_steps", "review_checks"],
            "all_passed": False,
            "issues": [
                {
                    "code": "blocking_check_failed",
                    "severity": "high",
                    "message": "Blocking-проверка не пройдена (авто-слой)",
                }
            ],
            "context_refs": [f"run:{review_run_id}"],
            "next_event": "review_failed",
        }
    else:
        payload = {
            "item": item,
            "run_id": subject_run_id,
            "verdict": "approved",
            "checked_artifacts": [
                "file_changes",
                "run_steps",
                "sandbox_diff",
                "review_checks",
            ],
            "all_passed": True,
            "issues": [],
            "context_refs": [f"run:{review_run_id}"],
            "next_event": "review_passed",
        }
    return json.dumps(payload, ensure_ascii=False)


def _read_reviewer_prompt_template() -> str:
    p = Path(__file__).resolve().parent.parent / "prompts" / "reviewer_prompt_v2.txt"
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


_REVIEWER_PROMPT_V1 = _read_reviewer_prompt_template()


def _build_reviewer_prompt(
    *, wi: dict, files_block: str, diff_block: str, snapshot_block: str, history_block: str
) -> str:
    tpl = _REVIEWER_PROMPT_V1 or ""
    return tpl.format(
        kind=wi.get("kind") or "",
        title=(wi.get("title") or "").strip(),
        description=(wi.get("description") or "").strip(),
        files_block=files_block,
        diff_block=diff_block,
        snapshot_block=snapshot_block,
        history_block=history_block,
    ).strip()


def _fetch_workspace_snapshots_block(conn, wi_id: str) -> str:
    """
    Best-effort current workspace contents for declared files.
    Reviewer can reject empty/incomplete diffs; snapshots reduce false negatives.
    """
    rows = conn.execute(
        "SELECT path FROM work_item_files WHERE work_item_id = ? ORDER BY path",
        (wi_id,),
    ).fetchall()
    if not rows:
        return "(no declared files)\n"
    repo_root = Path(__file__).resolve().parent.parent.parent  # .../factory/agents -> .../proekt
    out: list[str] = []
    for r in rows[:10]:
        rel = (r["path"] or "").replace("\\", "/").strip()
        if not rel:
            continue
        p = (repo_root / rel).resolve()
        try:
            p.relative_to(repo_root.resolve())
        except ValueError:
            continue
        if p.is_file():
            try:
                raw = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                raw = ""
            raw = raw[:8000]
            out.append(f"### {rel}\n```\n{raw}\n```\n")
        else:
            out.append(f"### {rel}\n_(file not found in workspace)_\n")
    return "\n".join(out).strip() + "\n"


def _env_qwen_dry_run() -> bool:
    raw = os.environ.get("FACTORY_QWEN_DRY_RUN")
    if raw is None:
        return True
    s = raw.strip().lower()
    return s not in ("0", "false", "no", "off")


def _parse_reviewer_llm_verdict(raw: str) -> tuple[str, str]:
    """
    Возвращает (verdict, reason). verdict: 'approved' | 'rejected'.
    Формат: 'APPROVED' или 'REJECTED:<reason>' (без JSON).
    При сомнении — rejected.
    """
    t = (raw or "").strip()
    if not t:
        return "rejected", "empty"
    u = t.upper()
    if u.startswith("APPROVED"):
        return "approved", ""
    if u.startswith("REJECTED"):
        # поддержим "REJECTED:quality" и "REJECTED quality ..."
        rest = t[len("REJECTED") :].lstrip()
        if rest.startswith(":"):
            rest = rest[1:].lstrip()
        return "rejected", (rest or "other")[:200]
    return "rejected", "invalid_format"


def _review_result_from_simple_verdict(
    *,
    wi: dict,
    review_run_id: str,
    subject_run_id: str,
    verdict: str,
    reason: str,
) -> ReviewResult:
    item = _item_ref(wi.get("kind") or "", wi.get("id") or "")
    if verdict == "approved":
        payload = {
            "item": item,
            "run_id": subject_run_id,
            "verdict": "approved",
            "checked_artifacts": ["file_changes", "run_steps", "review_checks"],
            "all_passed": True,
            "issues": [],
            "context_refs": [f"run:{review_run_id}"],
            "next_event": "review_passed",
        }
        return ReviewResult.model_validate(payload)
    # rejected
    code = (reason or "other").strip()[:60] or "other"
    payload = {
        "item": item,
        "run_id": subject_run_id,
        "verdict": "rejected",
        "checked_artifacts": ["file_changes", "run_steps", "review_checks"],
        "all_passed": False,
        "issues": [
            {"code": code, "severity": "high", "message": (reason or "rejected")[:500]}
        ],
        "context_refs": [f"run:{review_run_id}"],
        "next_event": "review_failed",
    }
    return ReviewResult.model_validate(payload)


def _fetch_files_block(conn, wi_id: str) -> str:
    rows = conn.execute(
        """
        SELECT path, intent, COALESCE(description,'') AS description
        FROM work_item_files
        WHERE work_item_id = ?
        ORDER BY path
        """,
        (wi_id,),
    ).fetchall()
    if not rows:
        return "- (no declared files)\n"
    lines: list[str] = []
    for r in rows:
        p = (r["path"] or "").replace("\\", "/").strip()
        intent = (r["intent"] or "").strip().lower() or "modify"
        desc = (r["description"] or "").strip()
        s = f"- {p} ({intent})"
        if desc:
            s += f" — {desc[:200]}"
        lines.append(s)
    return "\n".join(lines) + "\n"


def _fetch_diff_block(conn, wi_id: str, subject_run_id: str | None) -> str:
    """
    Для ревью: unified diff summaries из file_changes последнего implement run.
    """
    if not subject_run_id:
        return "(no forge run / no diff available)\n"
    rows = conn.execute(
        """
        SELECT path, change_type, diff_summary
        FROM file_changes
        WHERE work_item_id = ? AND run_id = ?
        ORDER BY path
        """,
        (wi_id, subject_run_id),
    ).fetchall()
    if not rows:
        return "(no captured file_changes for this run)\n"
    chunks: list[str] = []
    for r in rows:
        p = (r["path"] or "").replace("\\", "/").strip()
        ct = (r["change_type"] or "").strip()
        d = (r["diff_summary"] or "").strip()
        chunks.append(f"--- {p} ({ct})\n{d}\n")
    out = "\n".join(chunks)
    if len(out) > 30000:
        out = out[:30000] + "\n... [diff truncated] ...\n"
    return out


def _fetch_review_history_block(conn, wi_id: str, *, limit: int = 3) -> str:
    rows = conn.execute(
        """
        SELECT verdict, reason_code, explanation, created_at
        FROM decisions
        WHERE work_item_id = ? AND decision_role = 'reviewer'
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (wi_id, limit),
    ).fetchall()
    if not rows:
        return "(no prior reviewer decisions)\n"
    lines: list[str] = []
    for r in rows:
        ts = (r["created_at"] or "")[:19]
        v = r["verdict"] or ""
        rc = r["reason_code"] or ""
        ex = (r["explanation"] or "").replace("\n", " ").strip()[:160]
        lines.append(f"- [{ts}] {v} ({rc}) {ex}".strip())
    return "\n".join(lines) + "\n"


def _insert_review_result_row(
    conn,
    *,
    row_id: str,
    reviewer_run_id: str,
    wi_id: str,
    result: ReviewResult,
) -> None:
    dump = result.model_dump()
    conn.execute(
        """
        INSERT INTO review_results (
            id, reviewer_run_id, work_item_id, subject_run_id, item, verdict,
            all_passed, next_event, issues_json, context_refs_json, payload_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row_id,
            reviewer_run_id,
            wi_id,
            result.run_id,
            result.item,
            result.verdict,
            1 if result.all_passed else 0,
            result.next_event,
            json.dumps([i.model_dump() for i in result.issues], ensure_ascii=False),
            json.dumps(result.context_refs, ensure_ascii=False),
            json.dumps(dump, ensure_ascii=False),
        ),
    )


def run_review(orchestrator: Orchestrator, item: dict) -> None:
    conn = orchestrator.conn
    sm = orchestrator.sm
    logger = orchestrator.logger
    accounts = orchestrator.accounts

    wi_id = item["work_item_id"]
    wi = conn.execute("SELECT * FROM work_items WHERE id = ?", (wi_id,)).fetchone()
    if not wi:
        return

    account = accounts.get_active_account()
    run_id = gen_id("run")
    lease_queue_row(conn, wi_id, Role.REVIEWER)
    insert_run(
        conn,
        run_id=run_id,
        wi_id=wi_id,
        role=Role.REVIEWER,
        run_type=RunType.REVIEW,
        account_id=account["account_id"],
        input_payload={
            "work_item_id": wi_id,
            "kind": wi["kind"],
            "status": wi["status"],
            "title": wi["title"],
        },
    )

    logger.log(
        EventType.RUN_STARTED,
        "run",
        run_id,
        "Run started (reviewer)",
        work_item_id=wi_id,
        run_id=run_id,
        caused_by_type="run",
        caused_by_id=run_id,
        actor_role=Role.REVIEWER.value,
        account_id=account["account_id"],
    )
    logger.log(
        EventType.REVIEW_STARTED,
        "work_item",
        wi_id,
        "Начато ревью (Фаза 2)",
        work_item_id=wi_id,
        run_id=run_id,
        caused_by_type="run",
        caused_by_id=run_id,
        actor_role=Role.REVIEWER.value,
        account_id=account["account_id"],
        tags=["review", "phase2"],
    )

    step_no = 1
    insert_run_step(
        conn,
        run_id,
        step_no,
        StepKind.PROMPT.value,
        {
            "step_kind": "prompt",
            "role": "reviewer",
            "description": "Автоматические проверки + ReviewResult (stub/LLM)",
            "input_summary": {"work_item_id": wi_id, "context_keys": ["work_item", "forge_run"]},
            "reasoning": "Фаза 2: авто-проверки затем STRICT JSON ReviewResult",
        },
        summary="prompt",
    )
    step_no += 1

    blocking_failed = False
    auto_checks = [
        (CheckType.TESTS.value, True),
        (CheckType.LINT.value, True),
        (CheckType.SECURITY.value, True),
    ]
    if env_force_review_reject():
        auto_checks = [
            (CheckType.TESTS.value, True),
            (CheckType.LINT.value, True),
            (CheckType.SECURITY.value, False),
        ]

    for check_type, ok in auto_checks:
        status = "passed" if ok else "failed"
        if not ok:
            blocking_failed = True
        conn.execute(
            """
            INSERT INTO review_checks
                (id, run_id, check_type, status, is_blocking, summary, details)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (
                gen_id("rc"),
                run_id,
                check_type,
                status,
                f"{check_type}: {status}",
                json.dumps({"layer": "auto"}, ensure_ascii=False),
            ),
        )
        logger.log(
            EventType.FORGE_STEP,
            "run",
            run_id,
            f"Проверка {check_type}: {status}",
            work_item_id=wi_id,
            run_id=run_id,
            severity=Severity.INFO if ok else Severity.WARN,
            payload={"check_type": check_type, "status": status, "layer": "review_check"},
            tags=["review_check"],
        )

    if not blocking_failed:
        for check_type in (CheckType.ARCHITECTURE.value, CheckType.POLICY.value):
            conn.execute(
                """
                INSERT INTO review_checks
                    (id, run_id, check_type, status, is_blocking, summary, details)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    gen_id("rc"),
                    run_id,
                    check_type,
                    "passed",
                    f"{check_type}: passed (stub LLM)",
                    json.dumps(
                        {"layer": "llm", "model": os.environ.get("FACTORY_REVIEW_MODEL", "stub")}
                    ),
                ),
            )

    latest_impl = _latest_implement_run_id(conn, wi_id)
    subject_for_json = latest_impl if latest_impl is not None else f"seed:{wi_id}"

    raw_text = ""
    if _env_qwen_dry_run():
        raw_text = _stub_review_raw_output(
            dict(wi),
            review_run_id=run_id,
            subject_run_id=subject_for_json,
            blocking_failed=blocking_failed,
        )
        if os.environ.get("FACTORY_REVIEW_FORCE_INVALID_JSON", "").strip() in (
            "1",
            "true",
            "yes",
        ):
            raw_text = '{"broken":'
    else:
        files_block = _fetch_files_block(conn, wi_id)
        diff_block = _fetch_diff_block(conn, wi_id, latest_impl)
        history_block = _fetch_review_history_block(conn, wi_id)
        snapshot_block = _fetch_workspace_snapshots_block(conn, wi_id)
        prompt = _build_reviewer_prompt(
            wi=dict(wi),
            files_block=files_block,
            diff_block=diff_block,
            snapshot_block=snapshot_block,
            history_block=history_block,
        )
        am = AccountManager(conn, logger)
        fr = run_qwen_cli(
            conn=conn,
            account_manager=am,
            logger=logger,
            work_item_id=wi_id,
            run_id=run_id,
            title="reviewer_review",
            description="",
            full_prompt=prompt,
            cwd=str(Path.cwd()),
        )
        combined = f"{fr.stdout or ''}\n{fr.stderr or ''}".strip()
        verdict, reason = _parse_reviewer_llm_verdict(combined) if fr.ok else ("rejected", "llm_error")
        # retry once if invalid format / empty (strict hint)
        if fr.ok and verdict == "rejected" and reason in ("invalid_format", "empty"):
            prompt2 = prompt + "\n\nОтветь строго в формате:\nAPPROVED\nили\nREJECTED:<reason>\nБез Markdown."
            fr2 = run_qwen_cli(
                conn=conn,
                account_manager=am,
                logger=logger,
                work_item_id=wi_id,
                run_id=run_id,
                title="reviewer_review_retry",
                description="",
                full_prompt=prompt2,
                cwd=str(Path.cwd()),
            )
            combined2 = f"{fr2.stdout or ''}\n{fr2.stderr or ''}".strip()
            verdict, reason = (
                _parse_reviewer_llm_verdict(combined2) if fr2.ok else ("rejected", "llm_error")
            )
            combined = combined2
        # конвертируем в STRICT JSON ReviewResult для БД/контракта
        rr_obj = _review_result_from_simple_verdict(
            wi=dict(wi),
            review_run_id=run_id,
            subject_run_id=subject_for_json,
            verdict=verdict,
            reason=reason or "",
        )
        raw_text = json.dumps(rr_obj.model_dump(), ensure_ascii=False)

    conn.execute(
        "UPDATE runs SET output_payload = ? WHERE id = ?",
        (raw_text, run_id),
    )

    insert_run_step(
        conn,
        run_id,
        step_no,
        StepKind.PROMPT.value,
        {
            "step_kind": "prompt",
            "role": "reviewer",
            "description": "Raw model output (STRICT JSON ReviewResult)",
            "raw_text_preview": raw_text[:2000],
        },
        summary="review_result_raw",
    )
    step_no += 1

    try:
        result = parse_review_result(raw_text)
        validate_review_fsm_alignment(result=result)
        validate_subject_run_alignment(
            result=result,
            latest_implement_run_id=latest_impl,
        )
    except ReviewResultValidationError as e:
        insert_run_step(
            conn,
            run_id,
            step_no,
            StepKind.ERROR.value,
            {
                "step_kind": "error",
                "role": "reviewer",
                "error": "ReviewResult validation failed",
                "detail": str(e),
                "raw_preview": raw_text[:4000],
            },
            summary="review_invalid_output",
        )
        logger.log(
            EventType.REVIEW_INVALID_OUTPUT,
            "run",
            run_id,
            f"Невалидный выход ревьюера: {e}",
            work_item_id=wi_id,
            run_id=run_id,
            actor_role=Role.REVIEWER.value,
            payload={"error": str(e), "raw_preview": raw_text[:4000]},
            tags=["review", "review_invalid_output"],
        )
        conn.execute(
            """
            UPDATE work_item_queue SET lease_owner = NULL, lease_until = NULL
            WHERE work_item_id = ?
            """,
            (wi_id,),
        )
        finish_run(
            conn,
            run_id,
            ok=False,
            error_summary=f"review_invalid_output: {e}",
            logger=logger,
            failure_event=EventType.REVIEW_INVALID_OUTPUT,
        )
        return

    rr_id = gen_id("rr")
    _insert_review_result_row(
        conn, row_id=rr_id, reviewer_run_id=run_id, wi_id=wi_id, result=result
    )

    logger.log(
        EventType.REVIEW_RESULT,
        "work_item",
        wi_id,
        "ReviewResult",
        work_item_id=wi_id,
        run_id=run_id,
        actor_role=Role.REVIEWER.value,
        payload=result.model_dump(),
        tags=["review", "review_result"],
    )

    insert_run_step(
        conn,
        run_id,
        step_no,
        StepKind.DECISION.value,
        {
            "step_kind": "decision",
            "role": "reviewer",
            "review_result": result.model_dump(),
        },
        summary=f"verdict {result.verdict} → {result.next_event}",
    )

    expl = (
        "Ревью пройдено"
        if result.verdict == "approved"
        else "Отклонено: см. issues в ReviewResult"
    )
    if result.verdict == "rejected" and result.issues:
        expl = result.issues[0].message[:500]

    conn.execute(
        """
        INSERT INTO decisions
            (id, work_item_id, run_id, decision_role, verdict, reason_code, explanation)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            gen_id("dec"),
            wi_id,
            run_id,
            Role.REVIEWER.value,
            DecisionVerdict.REJECTED.value
            if result.verdict == "rejected"
            else DecisionVerdict.APPROVED.value,
            result.issues[0].code if result.verdict == "rejected" and result.issues else "other",
            expl,
        ),
    )

    ok_fsm, msg_fsm = sm.apply_transition(
        wi_id,
        result.next_event,
        actor_role=Role.REVIEWER.value,
        run_id=run_id,
    )
    if not ok_fsm:
        conn.execute(
            """
            UPDATE work_item_queue SET lease_owner = NULL, lease_until = NULL
            WHERE work_item_id = ?
            """,
            (wi_id,),
        )
        finish_run(conn, run_id, ok=False, error_summary=msg_fsm, logger=logger)
        return

    if result.verdict == "approved":
        # После review_passed FSM теперь переводит в ready_for_judge и сам ставит в judge_inbox.
        # Поэтому не трогаем work_item_queue здесь (иначе можно стереть judge_inbox).
        logger.log(
            EventType.REVIEW_PASSED,
            "work_item",
            wi_id,
            "Ревью пройдено — готово к коммиту",
            work_item_id=wi_id,
            run_id=run_id,
            payload={"review_result": result.model_dump()},
            tags=["review"],
        )
    else:
        logger.log(
            EventType.REVIEW_REJECTED,
            "work_item",
            wi_id,
            "Ревью не пройдено",
            work_item_id=wi_id,
            run_id=run_id,
            severity=Severity.WARN,
            payload={"review_result": result.model_dump()},
            tags=["review"],
        )

    finish_run(conn, run_id, ok=True, logger=logger)
