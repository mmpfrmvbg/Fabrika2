"""Судья: STRICT JSON ``JudgeVerdict`` → валидация → FSM (см. docs/PHASE2_AGENT_CONTRACT.md §8).

События переходов задаются в seed ``state_transitions``; здесь выбирается ``next_event`` из
валидного вердикта (после согласования с ``kind`` work item).
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import AccountManager
from ..contracts.judge import (
    JudgeVerdict,
    JudgeVerdictValidationError,
    parse_judge_verdict,
    validate_verdict_fsm_alignment,
)
from ..db import gen_id
from ..models import EventType, Role, RunType, Severity, StepKind, WorkItemKind
from ..qwen_cli_runner import run_qwen_cli
from ._helpers import finish_run, insert_run, insert_run_step, lease_queue_row

if TYPE_CHECKING:
    from ..orchestrator_core import Orchestrator


def _item_ref(wi_kind: str, wi_id: str) -> str:
    return f"{wi_kind}:{wi_id}"


def _fetch_prior_event_log_lines(
    conn: sqlite3.Connection, wi_id: str, *, limit: int = 20
) -> list[str]:
    """
    Последние ``limit`` событий ``event_log`` по work item (и шагам чужих run этого WI),
    от старых к новым — для блока промпта судьи. Вызывать **до** записи RUN_STARTED этого прогона.
    """
    rows = conn.execute(
        """
        SELECT event_time,
               COALESCE(actor_role, '') AS actor_role,
               event_type,
               message
        FROM event_log
        WHERE work_item_id = ?
           OR run_id IN (SELECT id FROM runs WHERE work_item_id = ?)
        ORDER BY event_time DESC, id DESC
        LIMIT ?
        """,
        (wi_id, wi_id, limit),
    ).fetchall()
    lines: list[str] = []
    for r in reversed(rows):
        ts = (r["event_time"] or "")[:19]
        actor = (r["actor_role"] or "system").strip() or "system"
        et = r["event_type"] or ""
        msg = (r["message"] or "").replace("\n", " ").strip()[:160]
        lines.append(f"- [{ts}] [{actor}] [{et}]: {msg}")
    return lines


def _format_recent_factory_events_block(lines: list[str]) -> str:
    body = "\n".join(lines) if lines else "_No prior events in unified journal for this work item._"
    return (
        "### Recent factory events for this work item\n\n"
        f"{body}\n"
    )


def _stub_judge_raw_output(wi: dict, *, used_event_log: bool = False) -> str:
    """
    Заглушка «выхода модели»: корректный JSON JudgeVerdict (без вызова LLM).
    Переменная окружения ``FACTORY_JUDGE_FORCE_INVALID_JSON=1`` — для тестов невалидного выхода.
    """
    if os.environ.get("FACTORY_JUDGE_FORCE_INVALID_JSON", "").strip() in ("1", "true", "yes"):
        return '{"broken":'

    kind = wi["kind"]
    wi_id = wi["id"]
    item = _item_ref(kind, wi_id)
    if kind in (WorkItemKind.ATOM.value, WorkItemKind.ATM_CHANGE.value):
        next_ev = "judge_approved"
        guards = ["phase2_stub_guard", "guard_has_files_declared"]
    else:
        next_ev = "judge_approved_for_planning"
        guards = ["phase2_stub_guard"]

    payload = {
        "item": item,
        "verdict": "approved",
        "checked_guards": guards,
        "all_passed": True,
        "context_refs": [],
        "next_event": next_ev,
        "used_event_log": used_event_log,
    }
    return json.dumps(payload, ensure_ascii=False)


def _read_judge_prompt_template() -> str:
    p = Path(__file__).resolve().parent.parent / "prompts" / "judge_prompt_v2.txt"
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


_JUDGE_PROMPT_V1 = _read_judge_prompt_template()


def _env_qwen_dry_run() -> bool:
    raw = os.environ.get("FACTORY_QWEN_DRY_RUN")
    if raw is None:
        return True
    s = raw.strip().lower()
    return s not in ("0", "false", "no", "off")


def _fetch_parent_chain(conn: sqlite3.Connection, wi_id: str, *, limit: int = 12) -> list[dict]:
    """
    Цепочка родителей от work item до корня (vision/initiative/...), включая текущий item.
    """
    chain: list[dict] = []
    cur = wi_id
    for _ in range(limit):
        row = conn.execute(
            "SELECT id, parent_id, kind, title, status FROM work_items WHERE id = ?",
            (cur,),
        ).fetchone()
        if not row:
            break
        chain.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "title": row["title"],
                "status": row["status"],
            }
        )
        pid = row["parent_id"]
        if not pid:
            break
        cur = pid
    return chain


def _fetch_latest_review_result(conn: sqlite3.Connection, wi_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT verdict, issues_json, payload_json, created_at
        FROM review_results
        WHERE work_item_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (wi_id,),
    ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except json.JSONDecodeError:
        payload = {}
    return {
        "verdict": row["verdict"],
        "created_at": row["created_at"],
        "payload": payload,
    }


def _fetch_siblings(conn: sqlite3.Connection, wi_id: str, *, limit: int = 12) -> list[dict]:
    pid_row = conn.execute(
        "SELECT parent_id FROM work_items WHERE id = ?",
        (wi_id,),
    ).fetchone()
    if not pid_row or not pid_row["parent_id"]:
        return []
    pid = pid_row["parent_id"]
    rows = conn.execute(
        """
        SELECT id, kind, title, status
        FROM work_items
        WHERE parent_id = ? AND id != ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (pid, wi_id, limit),
    ).fetchall()
    return [
        {"id": r["id"], "kind": r["kind"], "title": r["title"], "status": r["status"]}
        for r in rows
    ]


def _fetch_declared_files_block(conn: sqlite3.Connection, wi_id: str) -> str:
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
    out: list[str] = []
    for r in rows:
        p = (r["path"] or "").replace("\\", "/").strip()
        intent = (r["intent"] or "").strip().lower()
        desc = (r["description"] or "").strip()
        s = f"- {p} ({intent})"
        if desc:
            s += f" — {desc[:200]}"
        out.append(s)
    return "\n".join(out) + "\n"


def _fetch_forge_artifacts_block(conn: sqlite3.Connection, wi_id: str) -> str:
    """
    Provide judge with minimal concrete evidence: diff summaries + current workspace snapshots
    of declared files (small caps).
    """
    # Latest forge implement run
    r = conn.execute(
        """
        SELECT id FROM runs
        WHERE work_item_id = ? AND role = 'forge' AND run_type = 'implement'
        ORDER BY started_at DESC LIMIT 1
        """,
        (wi_id,),
    ).fetchone()
    run_id = r["id"] if r else None

    chunks: list[str] = []
    if run_id:
        rows = conn.execute(
            """
            SELECT path, change_type, COALESCE(diff_summary,'') AS diff_summary
            FROM file_changes
            WHERE work_item_id = ? AND run_id = ?
            ORDER BY path
            """,
            (wi_id, run_id),
        ).fetchall()
        if rows:
            chunks.append("### Diff summaries (latest forge run)\n")
            for rr in rows:
                p = (rr["path"] or "").replace("\\", "/").strip()
                ct = (rr["change_type"] or "").strip()
                ds = (rr["diff_summary"] or "").strip()
                chunks.append(f"- {p} ({ct})")
                if ds:
                    chunks.append(ds[:2000])
                chunks.append("")

    # Workspace snapshots for declared files (best-effort)
    decl = conn.execute(
        "SELECT path FROM work_item_files WHERE work_item_id = ? ORDER BY path",
        (wi_id,),
    ).fetchall()
    repo_root = Path(__file__).resolve().parent.parent.parent  # .../factory/agents -> .../proekt
    if decl:
        chunks.append("### Workspace snapshots (declared files)\n")
        for d in decl[:10]:
            rel = (d["path"] or "").replace("\\", "/").strip()
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
                chunks.append(f"#### {rel}\n```\n{raw}\n```\n")
            else:
                chunks.append(f"#### {rel}\n_(file not found in workspace)_\n")

    body = "\n".join(chunks).strip()
    return body if body else "(no forge artifacts / no snapshots available)\n"


def _build_judge_prompt(
    *,
    chain: list[dict],
    wi: dict,
    review_result: dict | None,
    journal_block: str,
    siblings: list[dict],
    files_block: str,
    artifacts_block: str,
) -> str:
    tpl = _JUDGE_PROMPT_V1 or ""
    chain_lines = "\n".join(
        f"- {c['kind']}:{c['id']} [{c['status']}] {str(c.get('title') or '')[:120]}"
        for c in chain
    ) or "- (no chain)\n"
    rr = json.dumps(review_result or {}, ensure_ascii=False)[:8000]
    sib = "\n".join(
        f"- {s['kind']}:{s['id']} [{s['status']}] {str(s.get('title') or '')[:120]}"
        for s in siblings
    ) or "- (no siblings)\n"
    return tpl.format(
        wi_kind=wi.get("kind") or "",
        wi_title=(wi.get("title") or "").strip(),
        wi_description=(wi.get("description") or "").strip(),
        chain_block=chain_lines,
        review_block=rr,
        files_block=files_block.strip(),
        artifacts_block=artifacts_block.strip(),
        journal_block=journal_block,
        siblings_block=sib,
    ).strip()


def _parse_judge_text_response(raw: str) -> dict[str, str]:
    """
    Парсит текстовый формат:
    DECISION: APPROVED|REJECTED|NEEDS_CHANGES|DEFERRED
    REASONCODE: ...
    EXPLANATION: ...
    SUGGESTED_FIX: ...
    """
    out: dict[str, str] = {}
    for line in (raw or "").splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        kk = k.strip().upper()
        if kk in ("DECISION", "REASONCODE", "EXPLANATION", "SUGGESTED_FIX"):
            out[kk] = v.strip()
    return out


def _verdict_from_parsed_text(*, wi_kind: str, parsed: dict[str, str], used_event_log: bool) -> JudgeVerdict:
    decision = (parsed.get("DECISION") or "").strip().upper()
    reason = (parsed.get("REASONCODE") or "other").strip()[:60] or "other"
    expl = (parsed.get("EXPLANATION") or "").strip()[:500]
    sug = (parsed.get("SUGGESTED_FIX") or "").strip()[:500] or None

    if decision == "APPROVED":
        next_ev = "judge_approved" if wi_kind in (WorkItemKind.ATOM.value, WorkItemKind.ATM_CHANGE.value) else "judge_approved_for_planning"
        payload = {
            "item": "",  # caller fills
            "verdict": "approved",
            "checked_guards": ["llm_judge_v1"],
            "all_passed": True,
            "context_refs": [],
            "next_event": next_ev,
            "used_event_log": used_event_log,
        }
        return JudgeVerdict.model_validate(payload)

    # Any non-approved decision => reject (safety-first)
    payload = {
        "item": "",  # caller fills
        "verdict": "rejected",
        "checked_guards": ["llm_judge_v1"],
        "all_passed": False,
        "context_refs": [],
        "next_event": "judge_rejected",
        "failed_guards": ["llm_judge_v1"],
        "rejection_reason_code": reason,
        "suggested_action": sug or expl or None,
        "used_event_log": used_event_log,
    }
    return JudgeVerdict.model_validate(payload)


def _insert_judge_verdict_row(
    conn,
    *,
    verdict_id: str,
    run_id: str,
    wi_id: str,
    verdict: JudgeVerdict,
) -> None:
    dump = verdict.model_dump()
    conn.execute(
        """
        INSERT INTO judge_verdicts (
            id, run_id, work_item_id, item, verdict, all_passed, next_event,
            rejection_reason_code, checked_guards_json, failed_guards_json,
            context_refs_json, suggested_action, payload_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            verdict_id,
            run_id,
            wi_id,
            verdict.item,
            verdict.verdict,
            1 if verdict.all_passed else 0,
            verdict.next_event,
            verdict.rejection_reason_code,
            json.dumps(verdict.checked_guards, ensure_ascii=False),
            json.dumps(verdict.failed_guards, ensure_ascii=False)
            if verdict.failed_guards is not None
            else None,
            json.dumps(verdict.context_refs, ensure_ascii=False),
            verdict.suggested_action,
            json.dumps(dump, ensure_ascii=False),
        ),
    )


def run_judge(orchestrator: Orchestrator, item: dict) -> None:
    conn = orchestrator.conn
    sm = orchestrator.sm
    logger = orchestrator.logger
    accounts = orchestrator.accounts

    wi_id = item["work_item_id"]
    wi = conn.execute("SELECT * FROM work_items WHERE id = ?", (wi_id,)).fetchone()
    if not wi:
        return

    prior_journal_lines = _fetch_prior_event_log_lines(conn, wi_id, limit=20)
    journal_block = _format_recent_factory_events_block(prior_journal_lines)
    used_journal = len(prior_journal_lines) > 0

    account = accounts.get_active_account()
    run_id = gen_id("run")
    lease_queue_row(conn, wi_id, Role.JUDGE)
    insert_run(
        conn,
        run_id=run_id,
        wi_id=wi_id,
        role=Role.JUDGE,
        run_type=RunType.JUDGE,
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
        "Run started (judge)",
        work_item_id=wi_id,
        run_id=run_id,
        caused_by_type="run",
        caused_by_id=run_id,
        actor_role=Role.JUDGE.value,
        account_id=account["account_id"],
    )
    logger.log(
        EventType.JUDGE_STARTED,
        "work_item",
        wi_id,
        "Судья: разбор JudgeVerdict (Фаза 2)",
        work_item_id=wi_id,
        run_id=run_id,
        caused_by_type="run",
        caused_by_id=run_id,
        actor_role=Role.JUDGE.value,
        tags=["judge", "phase2"],
    )

    raw_text = _stub_judge_raw_output(dict(wi), used_event_log=used_journal)
    if not _env_qwen_dry_run():
        chain = _fetch_parent_chain(conn, wi_id)
        rr = _fetch_latest_review_result(conn, wi_id)
        siblings = _fetch_siblings(conn, wi_id)
        files_block = _fetch_declared_files_block(conn, wi_id)
        artifacts_block = _fetch_forge_artifacts_block(conn, wi_id)
        prompt = _build_judge_prompt(
            chain=chain,
            wi=dict(wi),
            review_result=rr,
            journal_block=journal_block,
            siblings=siblings,
            files_block=files_block,
            artifacts_block=artifacts_block,
        )
        am = AccountManager(conn, logger)
        fr = run_qwen_cli(
            conn=conn,
            account_manager=am,
            logger=logger,
            work_item_id=wi_id,
            run_id=run_id,
            title="judge_verdict",
            description="",
            full_prompt=prompt,
            cwd=str(Path.cwd()),
        )
        combined = f"{fr.stdout or ''}\n{fr.stderr or ''}".strip()
        if not fr.ok or not combined:
            # LLM error => reject (safety)
            verdict_obj = JudgeVerdict.model_validate(
                {
                    "item": _item_ref(wi["kind"], wi_id),
                    "verdict": "rejected",
                    "checked_guards": ["llm_error"],
                    "all_passed": False,
                    "context_refs": [],
                    "next_event": "judge_rejected",
                    "failed_guards": ["llm_error"],
                    "rejection_reason_code": "llm_error",
                    "suggested_action": (fr.error_message or "llm_error")[:200],
                    "used_event_log": used_journal,
                }
            )
            raw_text = json.dumps(verdict_obj.model_dump(), ensure_ascii=False)
        else:
            parsed = _parse_judge_text_response(combined)
            # retry once on invalid/incomplete parse
            if not parsed.get("DECISION"):
                prompt2 = prompt + "\n\nОтветь строго 4 строками:\nDECISION: ...\nREASONCODE: ...\nEXPLANATION: ...\nSUGGESTED_FIX: ...\nБез Markdown."
                fr2 = run_qwen_cli(
                    conn=conn,
                    account_manager=am,
                    logger=logger,
                    work_item_id=wi_id,
                    run_id=run_id,
                    title="judge_verdict_retry",
                    description="",
                    full_prompt=prompt2,
                    cwd=str(Path.cwd()),
                )
                combined2 = f"{fr2.stdout or ''}\n{fr2.stderr or ''}".strip()
                if not fr2.ok or not combined2:
                    verdict_obj = JudgeVerdict.model_validate(
                        {
                            "item": _item_ref(wi["kind"], wi_id),
                            "verdict": "rejected",
                            "checked_guards": ["llm_error"],
                            "all_passed": False,
                            "context_refs": [],
                            "next_event": "judge_rejected",
                            "failed_guards": ["llm_error"],
                            "rejection_reason_code": "llm_error",
                            "suggested_action": (fr2.error_message or "llm_error")[:200],
                            "used_event_log": used_journal,
                        }
                    )
                    raw_text = json.dumps(verdict_obj.model_dump(), ensure_ascii=False)
                else:
                    parsed = _parse_judge_text_response(combined2)
            verdict_obj = _verdict_from_parsed_text(wi_kind=wi["kind"], parsed=parsed, used_event_log=used_journal)
            # fill item now
            verdict_obj = JudgeVerdict.model_validate(
                {**verdict_obj.model_dump(), "item": _item_ref(wi["kind"], wi_id)}
            )
            raw_text = json.dumps(verdict_obj.model_dump(), ensure_ascii=False)

    conn.execute(
        "UPDATE runs SET output_payload = ? WHERE id = ?",
        (raw_text, run_id),
    )

    insert_run_step(
        conn,
        run_id,
        1,
        StepKind.PROMPT.value,
        {
            "step_kind": "prompt",
            "role": "judge",
            "description": "Prompt context + raw model output (STRICT JSON JudgeVerdict)",
            "input_summary": {"work_item_id": wi_id},
            "journal_context_block": journal_block,
            "recent_factory_events_preview": prior_journal_lines,
            "raw_text_preview": raw_text[:2000],
        },
    )

    try:
        verdict = parse_judge_verdict(raw_text)
        validate_verdict_fsm_alignment(work_item_kind=wi["kind"], verdict=verdict)
    except JudgeVerdictValidationError as e:
        insert_run_step(
            conn,
            run_id,
            2,
            StepKind.ERROR.value,
            {
                "step_kind": "error",
                "role": "judge",
                "error": "JudgeVerdict validation failed",
                "detail": str(e),
                "raw_preview": raw_text[:4000],
            },
            summary="judge_invalid_output",
        )
        logger.log(
            EventType.JUDGE_INVALID_OUTPUT,
            "run",
            run_id,
            f"Невалидный выход судьи: {e}",
            work_item_id=wi_id,
            run_id=run_id,
            actor_role=Role.JUDGE.value,
            payload={"error": str(e), "raw_preview": raw_text[:4000]},
            tags=["judge", "judge_invalid_output"],
        )
        logger.log(
            EventType.FORGE_STEP,
            "run",
            run_id,
            "judge.llm_or_parse_error",
            work_item_id=wi_id,
            run_id=run_id,
            actor_role=Role.JUDGE.value,
            severity=Severity.ERROR,
            payload={"error": str(e)},
            tags=["judge", "llm_error"],
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
            error_summary=f"judge_invalid_output: {e}",
            logger=logger,
            failure_event=EventType.JUDGE_INVALID_OUTPUT,
        )
        return

    verdict_id = gen_id("jv")
    _insert_judge_verdict_row(conn, verdict_id=verdict_id, run_id=run_id, wi_id=wi_id, verdict=verdict)

    # Persist judge feedback for Forge retry prompts.
    # Forge prompt reads `decisions` (and `comments`); JudgeVerdict alone is not used by Forge.
    conn.execute(
        """
        INSERT INTO decisions
            (id, work_item_id, run_id, decision_role, verdict, reason_code, explanation, suggested_fix)
        VALUES (?, ?, ?, 'judge', ?, ?, ?, ?)
        """,
        (
            gen_id("dec"),
            wi_id,
            run_id,
            verdict.verdict,
            (verdict.rejection_reason_code or "")[:60]
            if verdict.verdict == "rejected"
            else None,
            (verdict.suggested_action or "")[:4000]
            if verdict.verdict == "rejected"
            else None,
            (verdict.suggested_action or "")[:4000]
            if verdict.verdict == "rejected"
            else None,
        ),
    )

    logger.log(
        EventType.JUDGE_VERDICT,
        "work_item",
        wi_id,
        "JudgeVerdict",
        work_item_id=wi_id,
        run_id=run_id,
        actor_role=Role.JUDGE.value,
        payload=verdict.model_dump(),
        tags=["judge", "judge_verdict"],
    )

    insert_run_step(
        conn,
        run_id,
        2,
        StepKind.DECISION.value,
        {
            "step_kind": "decision",
            "role": "judge",
            "judge_verdict": verdict.model_dump(),
        },
        summary=f"verdict {verdict.verdict} → {verdict.next_event}",
    )

    event = verdict.next_event
    ok, msg = sm.apply_transition(
        wi_id,
        event,
        actor_role=Role.JUDGE.value,
        run_id=run_id,
    )
    if ok:
        if verdict.verdict == "approved":
            logger.log(
                EventType.JUDGE_APPROVED,
                "work_item",
                wi_id,
                f"Судья одобрил: {event}",
                work_item_id=wi_id,
                run_id=run_id,
                payload={"event": event, "msg": msg, "verdict": verdict.model_dump()},
                tags=["judge"],
            )
        else:
            logger.log(
                EventType.JUDGE_REJECTED,
                "work_item",
                wi_id,
                f"Судья отклонил: {event}",
                work_item_id=wi_id,
                run_id=run_id,
                payload={"event": event, "msg": msg, "verdict": verdict.model_dump()},
                tags=["judge"],
            )
    else:
        conn.execute(
            """
            UPDATE work_item_queue SET lease_owner = NULL, lease_until = NULL
            WHERE work_item_id = ?
            """,
            (wi_id,),
        )
    finish_run(conn, run_id, ok=ok, error_summary=None if ok else msg, logger=logger)
