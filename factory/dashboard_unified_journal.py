"""
Unified Journal — read-only projection поверх event_log, runs (не дублируем), run_steps,
file_changes, comments, architect_comments, decisions.

Канон сортировки: ts DESC, затем source_type ASC (comment < event < file_change < run_step),
затем source_id DESC (строковое сравнение — детерминированный tie-break).

architect_comments и decisions отображаются как source_type ``comment`` с префиксом source_id
(``a:…``, ``d:…``), чтобы не пересекаться с ``comments.id``.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from functools import cmp_to_key
from typing import Any

from .contracts.events import JOURNAL_SCHEMA_VERSION, enrich_journal_items

# При равном ts: сначала «комментарии», затем события, шаги прогона, файлы (стабильно)
_SOURCE_TYPE_RANK: dict[str, int] = {
    "comment": 0,
    "event": 1,
    "file_change": 2,
    "run_step": 3,
}


def _rank_source_type(st: str) -> int:
    return _SOURCE_TYPE_RANK.get(st, 99)


def _parse_json_maybe(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}


def _event_type_to_kind(event_type: str) -> str:
    et = (event_type or "").strip().lower()
    if et == "task.status_changed":
        return "transition"
    if et in ("task.enqueued",):
        return "queue_enqueue"
    if et in ("task.dequeued",):
        return "queue_dequeue"
    if et in ("run.started",):
        return "run_started"
    if et in ("run.completed",):
        return "run_completed"
    if et in ("run.failed",) or et.startswith("run.failed"):
        return "run_failed"
    if et in ("judge.approved", "judge_approved"):
        return "judge_approved"
    if et in ("judge.rejected", "judge_rejected"):
        return "judge_rejected"
    if et in ("judge.verdict",):
        return "judge_verdict"
    if et in ("judge.invalid_output",):
        return "judge_invalid_output"
    if et in ("forge.started", "forge_started"):
        return "forge_started"
    if et in ("forge.completed", "forge_completed", "forge.succeeded", "forge_succeeded"):
        return "forge_completed"
    if et in ("forge.failed", "forge_failed"):
        return "forge_failed"
    if et in ("forge.step",) or et.startswith("forge_"):
        return "forge_audit"
    if et in ("review.passed", "review_passed"):
        return "review_passed"
    if et in ("review.rejected", "review_rejected"):
        return "review_rejected"
    if et in ("review.result",):
        return "review_result"
    if et in ("review.invalid_output",):
        return "review_invalid_output"
    if et in ("comment.added", "comment_added"):
        return "comment_added"
    if et.startswith("dashboard.task_run"):
        return "dashboard_run"
    if et.startswith("account."):
        return "account"
    return et.replace(".", "_") if et else "event"


def _work_item_ids_subtree(conn: sqlite3.Connection, anchor_id: str) -> frozenset[str]:
    """Все work_item в поддереве с корнем anchor_id (включая сам anchor)."""
    aid = anchor_id.strip()
    try:
        rows = conn.execute(
            """
            WITH RECURSIVE sub(id) AS (
                SELECT ? AS id
                UNION ALL
                SELECT w.id FROM work_items w
                INNER JOIN sub ON w.parent_id = sub.id
            )
            SELECT id FROM sub
            """,
            (aid,),
        ).fetchall()
        return frozenset(str(r["id"]) for r in rows)
    except sqlite3.OperationalError:
        rows = conn.execute(
            "SELECT id FROM work_items WHERE root_id = ? OR id = ?",
            (aid, aid),
        ).fetchall()
        return frozenset(str(r["id"]) for r in rows)


def _work_item_ids_under_root(conn: sqlite3.Connection, root_id: str) -> frozenset[str]:
    """Поддерево от узла (Vision / Epic / …), не только совпадение колонки root_id."""
    return _work_item_ids_subtree(conn, root_id)


def _merge_payload(base: dict[str, Any], **extra: Any) -> dict[str, Any]:
    out = dict(base)
    for k, v in extra.items():
        if v is not None:
            out[k] = v
    return out


def _entry(
    *,
    ts: str,
    source_type: str,
    source_id: str,
    work_item_id: str | None,
    run_id: str | None,
    kind: str,
    title: str,
    summary: str,
    status_before: str | None,
    status_after: str | None,
    role: str | None,
    path: str | None,
    payload: Any,
) -> dict[str, Any]:
    sk = f"{source_type}:{source_id}"
    return {
        "ts": ts,
        "source_type": source_type,
        "source_id": source_id,
        "source_key": sk,
        "work_item_id": work_item_id,
        "run_id": run_id,
        "kind": kind,
        "title": title,
        "summary": summary,
        "status_before": status_before,
        "status_after": status_after,
        "role": (role or None),
        "path": path,
        "payload": payload,
    }


@dataclass(frozen=True)
class JournalFilters:
    work_item_id: str | None = None
    run_id: str | None = None
    root_id: str | None = None
    kind: str | None = None
    role: str | None = None

    def normalized_kind(self) -> str | None:
        k = (self.kind or "").strip()
        return k or None

    def normalized_role(self) -> str | None:
        r = (self.role or "").strip().lower()
        return r or None


def _wi_set(conn: sqlite3.Connection, flt: JournalFilters) -> frozenset[str] | None:
    """None = без ограничения по поддереву; иначе множество work_item id."""
    if flt.work_item_id:
        return frozenset({flt.work_item_id.strip()})
    if flt.root_id:
        return _work_item_ids_under_root(conn, flt.root_id)
    return None


def _event_log_wheres(
    flt: JournalFilters, wi_set: frozenset[str] | None
) -> tuple[str, list[Any]]:
    wheres: list[str] = []
    params: list[Any] = []

    if flt.run_id:
        rid = flt.run_id.strip()
        wheres.append("(run_id = ? OR (entity_type = 'run' AND entity_id = ?))")
        params.extend([rid, rid])

    if wi_set is not None:
        if not wi_set:
            return "1=0", []
        ph = ",".join("?" * len(wi_set))
        wheres.append(
            f"(work_item_id IN ({ph}) "
            f"OR (entity_type = 'work_item' AND entity_id IN ({ph})) "
            f"OR run_id IN (SELECT id FROM runs WHERE work_item_id IN ({ph})))"
        )
        ids = list(wi_set)
        params.extend(ids)
        params.extend(ids)
        params.extend(ids)

    where_sql = (" AND " + " AND ".join(wheres)) if wheres else ""
    return where_sql, params


def _rows_event_log(
    conn: sqlite3.Connection, flt: JournalFilters, wi_set: frozenset[str] | None
) -> list[sqlite3.Row]:
    extra, params = _event_log_wheres(flt, wi_set)
    sql = f"SELECT * FROM event_log WHERE 1=1{extra} ORDER BY event_time DESC, id DESC"
    try:
        return list(conn.execute(sql, params).fetchall())
    except sqlite3.OperationalError:
        return []


def _rows_run_steps(
    conn: sqlite3.Connection, flt: JournalFilters, wi_set: frozenset[str] | None
) -> list[sqlite3.Row]:
    wheres: list[str] = []
    params: list[Any] = []
    if flt.run_id:
        wheres.append("rs.run_id = ?")
        params.append(flt.run_id.strip())
    if wi_set is not None:
        if not wi_set:
            return []
        ph = ",".join("?" * len(wi_set))
        wheres.append(f"r.work_item_id IN ({ph})")
        params.extend(wi_set)
    where_sql = (" AND " + " AND ".join(wheres)) if wheres else ""
    sql = f"""
        SELECT rs.*, r.work_item_id AS _wi, r.role AS _run_role
        FROM run_steps rs
        INNER JOIN runs r ON r.id = rs.run_id
        WHERE 1=1 {where_sql}
        ORDER BY rs.created_at DESC, rs.run_id DESC, rs.step_no DESC
    """
    try:
        return list(conn.execute(sql, params).fetchall())
    except sqlite3.OperationalError:
        return []


def _rows_file_changes(
    conn: sqlite3.Connection, flt: JournalFilters, wi_set: frozenset[str] | None
) -> list[sqlite3.Row]:
    wheres: list[str] = []
    params: list[Any] = []
    if flt.run_id:
        wheres.append("run_id = ?")
        params.append(flt.run_id.strip())
    if wi_set is not None:
        if not wi_set:
            return []
        ph = ",".join("?" * len(wi_set))
        wheres.append(f"work_item_id IN ({ph})")
        params.extend(wi_set)
    where_sql = (" AND " + " AND ".join(wheres)) if wheres else ""
    sql = f"""
        SELECT * FROM file_changes
        WHERE 1=1 {where_sql}
        ORDER BY created_at DESC, id DESC
    """
    try:
        return list(conn.execute(sql, params).fetchall())
    except sqlite3.OperationalError:
        return []


def _rows_comments(
    conn: sqlite3.Connection, flt: JournalFilters, wi_set: frozenset[str] | None
) -> list[sqlite3.Row]:
    if flt.run_id:
        return []
    wheres: list[str] = []
    params: list[Any] = []
    if wi_set is not None:
        if not wi_set:
            return []
        ph = ",".join("?" * len(wi_set))
        wheres.append(f"work_item_id IN ({ph})")
        params.extend(wi_set)
    where_sql = (" AND " + " AND ".join(wheres)) if wheres else ""
    sql = f"SELECT * FROM comments WHERE 1=1 {where_sql} ORDER BY created_at DESC, id DESC"
    try:
        return list(conn.execute(sql, params).fetchall())
    except sqlite3.OperationalError:
        return []


def _rows_architect_comments(
    conn: sqlite3.Connection, flt: JournalFilters, wi_set: frozenset[str] | None
) -> list[sqlite3.Row]:
    if flt.run_id:
        return []
    wheres: list[str] = []
    params: list[Any] = []
    if wi_set is not None:
        if not wi_set:
            return []
        ph = ",".join("?" * len(wi_set))
        wheres.append(f"work_item_id IN ({ph})")
        params.extend(wi_set)
    where_sql = (" AND " + " AND ".join(wheres)) if wheres else ""
    sql = f"""
        SELECT * FROM architect_comments
        WHERE 1=1 {where_sql}
        ORDER BY created_at DESC, id DESC
    """
    try:
        return list(conn.execute(sql, params).fetchall())
    except sqlite3.OperationalError:
        return []


def _rows_decisions(
    conn: sqlite3.Connection, flt: JournalFilters, wi_set: frozenset[str] | None
) -> list[sqlite3.Row]:
    wheres: list[str] = []
    params: list[Any] = []
    if flt.run_id:
        wheres.append("run_id = ?")
        params.append(flt.run_id.strip())
    if wi_set is not None:
        if not wi_set:
            return []
        ph = ",".join("?" * len(wi_set))
        wheres.append(f"work_item_id IN ({ph})")
        params.extend(wi_set)
    where_sql = (" AND " + " AND ".join(wheres)) if wheres else ""
    sql = f"SELECT * FROM decisions WHERE 1=1 {where_sql} ORDER BY created_at DESC, id DESC"
    try:
        return list(conn.execute(sql, params).fetchall())
    except sqlite3.OperationalError:
        return []


def _event_row_to_entry(r: sqlite3.Row) -> dict[str, Any]:
    keys = r.keys()
    pl_raw = r["payload"] if "payload" in keys else None
    pl = _parse_json_maybe(pl_raw)
    et = r["event_type"]
    kind = _event_type_to_kind(et)
    sb = sa = None
    if isinstance(pl, dict):
        sb = pl.get("from_status") or pl.get("status_before") or pl.get("previous_status")
        sa = pl.get("to_status") or pl.get("status_after") or pl.get("new_status")
    title = str(et or "event")
    summary = (r["message"] or "").replace("\n", " ")
    payload: Any = pl if isinstance(pl, dict) else (pl or {})
    if not isinstance(payload, dict):
        payload = {"value": payload}
    sev = r["severity"] if "severity" in keys else None
    payload = _merge_payload(
        payload,
        event_log_id=r["id"],
        event_type=et,
        severity=sev,
        entity_type=r["entity_type"],
        entity_id=r["entity_id"],
    )
    return _entry(
        ts=r["event_time"],
        source_type="event",
        source_id=str(r["id"]),
        work_item_id=r["work_item_id"],
        run_id=r["run_id"],
        kind=kind,
        title=title,
        summary=summary,
        status_before=str(sb) if sb is not None else None,
        status_after=str(sa) if sa is not None else None,
        role=r["actor_role"] if "actor_role" in keys else None,
        path=None,
        payload=payload,
    )


def _run_step_row_to_entry(r: sqlite3.Row) -> dict[str, Any]:
    sk = r["step_kind"]
    pl = _parse_json_maybe(r["payload"])
    if not isinstance(pl, dict):
        pl = {"raw": pl}
    pl = _merge_payload(
        pl,
        step_no=r["step_no"],
        step_kind=sk,
        step_status=r["status"],
        duration_ms=r["duration_ms"] if "duration_ms" in r.keys() else None,
    )
    summary = (r["summary"] or "")[:2000] or str(sk)
    return _entry(
        ts=r["created_at"],
        source_type="run_step",
        source_id=str(r["id"]),
        work_item_id=r["_wi"],
        run_id=r["run_id"],
        kind=f"run_step_{sk}",
        title=f"Step {r['step_no']}: {sk}",
        summary=summary,
        status_before=None,
        status_after=str(r["status"]) if r["status"] else None,
        role=r["_run_role"],
        path=None,
        payload=pl,
    )


def _file_change_row_to_entry(r: sqlite3.Row) -> dict[str, Any]:
    pl = {
        "id": r["id"],
        "change_type": r["change_type"],
        "old_hash": r["old_hash"],
        "new_hash": r["new_hash"],
        "diff_summary": r["diff_summary"],
        "diff_ref": r["diff_ref"] if "diff_ref" in r.keys() else None,
        "lines_added": r["lines_added"] if "lines_added" in r.keys() else None,
        "lines_removed": r["lines_removed"] if "lines_removed" in r.keys() else None,
    }
    sm = (r["diff_summary"] or "") or str(r["change_type"])
    return _entry(
        ts=r["created_at"],
        source_type="file_change",
        source_id=str(r["id"]),
        work_item_id=r["work_item_id"],
        run_id=r["run_id"],
        kind="file_change",
        title=str(r["path"]),
        summary=sm[:2000],
        status_before=None,
        status_after=None,
        role="forge",
        path=str(r["path"]),
        payload=pl,
    )


def _comment_row_to_entry(r: sqlite3.Row) -> dict[str, Any]:
    ct = (r["comment_type"] or "note").lower()
    ar = r["author_role"]
    kind = "review_comment" if ct in ("rejection", "summary") and ar == "reviewer" else (
        "judge_comment" if ar == "judge" else f"comment_{ct}"
    )
    spl = _parse_json_maybe(r["structured_payload"] if "structured_payload" in r.keys() else None)
    pl = spl if isinstance(spl, dict) else {}
    pl = _merge_payload(
        pl,
        comment_id=r["id"],
        comment_type=r["comment_type"],
        body=r["body"],
        author_role=ar,
        journal_origin="comments",
    )
    body = (r["body"] or "")[:2000]
    return _entry(
        ts=r["created_at"],
        source_type="comment",
        source_id=f"c:{r['id']}",
        work_item_id=r["work_item_id"],
        run_id=None,
        kind=kind,
        title=f"{ar}: {ct}",
        summary=body,
        status_before=None,
        status_after=None,
        role=ar,
        path=None,
        payload=pl,
    )


def _architect_comment_row_to_entry(r: sqlite3.Row) -> dict[str, Any]:
    txt = (r["comment"] or "")[:2000]
    pl = {"id": r["id"], "comment": r["comment"], "journal_origin": "architect_comments"}
    return _entry(
        ts=r["created_at"],
        source_type="comment",
        source_id=f"a:{r['id']}",
        work_item_id=r["work_item_id"],
        run_id=None,
        kind="architect_comment",
        title="Architect note",
        summary=txt,
        status_before=None,
        status_after=None,
        role="architect",
        path=None,
        payload=pl,
    )


def _decision_row_to_entry(r: sqlite3.Row) -> dict[str, Any]:
    rk = r.keys()
    pl = {
        "id": r["id"],
        "verdict": r["verdict"],
        "reason_code": r["reason_code"],
        "explanation": r["explanation"],
        "suggested_fix": r["suggested_fix"] if "suggested_fix" in rk else None,
        "comment_id": r["comment_id"] if "comment_id" in rk else None,
        "journal_origin": "decisions",
    }
    expl = (r["explanation"] or "")[:2000]
    return _entry(
        ts=r["created_at"],
        source_type="comment",
        source_id=f"d:{r['id']}",
        work_item_id=r["work_item_id"],
        run_id=r["run_id"] if "run_id" in rk else None,
        kind="decision",
        title=f"Decision: {r['verdict']}",
        summary=expl or str(r["verdict"]),
        status_before=None,
        status_after=None,
        role=r["decision_role"],
        path=None,
        payload=pl,
    )


def _row_to_entries_dispatch(
    table: str, rows: list[sqlite3.Row]
) -> list[dict[str, Any]]:
    if table == "event_log":
        return [_event_row_to_entry(r) for r in rows]
    if table == "run_steps":
        return [_run_step_row_to_entry(r) for r in rows]
    if table == "file_changes":
        return [_file_change_row_to_entry(r) for r in rows]
    if table == "comments":
        return [_comment_row_to_entry(r) for r in rows]
    if table == "architect_comments":
        return [_architect_comment_row_to_entry(r) for r in rows]
    if table == "decisions":
        return [_decision_row_to_entry(r) for r in rows]
    return []


def _passes_kind_role(
    e: dict[str, Any], kind_pat: str | None, role_pat: str | None
) -> bool:
    if kind_pat:
        if e.get("kind") != kind_pat:
            return False
    if role_pat:
        er = (e.get("role") or "").lower()
        if er != role_pat:
            return False
    return True


def _cmp_journal_desc(a: dict[str, Any], b: dict[str, Any]) -> int:
    """Порядок: ts DESC, rank(source_type) ASC, source_id DESC."""
    ta, tb = (a.get("ts") or ""), (b.get("ts") or "")
    if ta > tb:
        return -1
    if ta < tb:
        return 1
    ra = _rank_source_type(str(a.get("source_type") or ""))
    rb = _rank_source_type(str(b.get("source_type") or ""))
    if ra < rb:
        return -1
    if ra > rb:
        return 1
    sa, sb = str(a.get("source_id") or ""), str(b.get("source_id") or "")
    if sa > sb:
        return -1
    if sa < sb:
        return 1
    return 0


def _count_event_log_sql(
    conn: sqlite3.Connection, flt: JournalFilters, wi_set: frozenset[str] | None
) -> int:
    extra, params = _event_log_wheres(flt, wi_set)
    try:
        return int(
            conn.execute(
                f"SELECT COUNT(*) AS c FROM event_log WHERE 1=1{extra}", params
            ).fetchone()["c"]
        )
    except sqlite3.OperationalError:
        return 0


def _count_run_steps_sql(
    conn: sqlite3.Connection, flt: JournalFilters, wi_set: frozenset[str] | None
) -> int:
    wheres: list[str] = []
    params: list[Any] = []
    if flt.run_id:
        wheres.append("rs.run_id = ?")
        params.append(flt.run_id.strip())
    if wi_set is not None:
        if not wi_set:
            return 0
        ph = ",".join("?" * len(wi_set))
        wheres.append(f"r.work_item_id IN ({ph})")
        params.extend(wi_set)
    where_sql = (" AND " + " AND ".join(wheres)) if wheres else ""
    try:
        return int(
            conn.execute(
                f"""
                SELECT COUNT(*) AS c FROM run_steps rs
                INNER JOIN runs r ON r.id = rs.run_id
                WHERE 1=1 {where_sql}
                """,
                params,
            ).fetchone()["c"]
        )
    except sqlite3.OperationalError:
        return 0


def _count_file_changes_sql(
    conn: sqlite3.Connection, flt: JournalFilters, wi_set: frozenset[str] | None
) -> int:
    wheres: list[str] = []
    params: list[Any] = []
    if flt.run_id:
        wheres.append("run_id = ?")
        params.append(flt.run_id.strip())
    if wi_set is not None:
        if not wi_set:
            return 0
        ph = ",".join("?" * len(wi_set))
        wheres.append(f"work_item_id IN ({ph})")
        params.extend(wi_set)
    where_sql = (" AND " + " AND ".join(wheres)) if wheres else ""
    try:
        return int(
            conn.execute(
                f"SELECT COUNT(*) AS c FROM file_changes WHERE 1=1 {where_sql}",
                params,
            ).fetchone()["c"]
        )
    except sqlite3.OperationalError:
        return 0


def _count_comments_sql(
    conn: sqlite3.Connection, flt: JournalFilters, wi_set: frozenset[str] | None
) -> int:
    if flt.run_id:
        return 0
    wheres: list[str] = []
    params: list[Any] = []
    if wi_set is not None:
        if not wi_set:
            return 0
        ph = ",".join("?" * len(wi_set))
        wheres.append(f"work_item_id IN ({ph})")
        params.extend(wi_set)
    where_sql = (" AND " + " AND ".join(wheres)) if wheres else ""
    try:
        return int(
            conn.execute(
                f"SELECT COUNT(*) AS c FROM comments WHERE 1=1 {where_sql}", params
            ).fetchone()["c"]
        )
    except sqlite3.OperationalError:
        return 0


def _count_architect_sql(
    conn: sqlite3.Connection, flt: JournalFilters, wi_set: frozenset[str] | None
) -> int:
    if flt.run_id:
        return 0
    wheres: list[str] = []
    params: list[Any] = []
    if wi_set is not None:
        if not wi_set:
            return 0
        ph = ",".join("?" * len(wi_set))
        wheres.append(f"work_item_id IN ({ph})")
        params.extend(wi_set)
    where_sql = (" AND " + " AND ".join(wheres)) if wheres else ""
    try:
        return int(
            conn.execute(
                f"SELECT COUNT(*) AS c FROM architect_comments WHERE 1=1 {where_sql}",
                params,
            ).fetchone()["c"]
        )
    except sqlite3.OperationalError:
        return 0


def _count_decisions_sql(
    conn: sqlite3.Connection, flt: JournalFilters, wi_set: frozenset[str] | None
) -> int:
    wheres: list[str] = []
    params: list[Any] = []
    if flt.run_id:
        wheres.append("run_id = ?")
        params.append(flt.run_id.strip())
    if wi_set is not None:
        if not wi_set:
            return 0
        ph = ",".join("?" * len(wi_set))
        wheres.append(f"work_item_id IN ({ph})")
        params.extend(wi_set)
    where_sql = (" AND " + " AND ".join(wheres)) if wheres else ""
    try:
        return int(
            conn.execute(
                f"SELECT COUNT(*) AS c FROM decisions WHERE 1=1 {where_sql}", params
            ).fetchone()["c"]
        )
    except sqlite3.OperationalError:
        return 0


def _collect_entries_for_merge(
    conn: sqlite3.Connection,
    flt: JournalFilters,
    wi_set: frozenset[str] | None,
    cap: int,
    kind_p: str | None,
    role_p: str | None,
) -> list[dict[str, Any]]:
    all_e: list[dict[str, Any]] = []
    for fetch_fn, name in (
        (_rows_event_log, "event_log"),
        (_rows_run_steps, "run_steps"),
        (_rows_file_changes, "file_changes"),
        (_rows_comments, "comments"),
        (_rows_architect_comments, "architect_comments"),
        (_rows_decisions, "decisions"),
    ):
        raw = fetch_fn(conn, flt, wi_set)[:cap]
        all_e.extend(_row_to_entries_dispatch(name, raw))
    return [e for e in all_e if _passes_kind_role(e, kind_p, role_p)]


def journal_count(conn: sqlite3.Connection, flt: JournalFilters) -> int:
    wi_set = _wi_set(conn, flt)
    if flt.work_item_id is None and flt.root_id is None and flt.run_id is None:
        wi_set = None
    elif wi_set is not None and not wi_set:
        return 0

    kind_p = flt.normalized_kind()
    role_p = flt.normalized_role()

    if not kind_p and not role_p:
        return (
            _count_event_log_sql(conn, flt, wi_set)
            + _count_run_steps_sql(conn, flt, wi_set)
            + _count_file_changes_sql(conn, flt, wi_set)
            + _count_comments_sql(conn, flt, wi_set)
            + _count_architect_sql(conn, flt, wi_set)
            + _count_decisions_sql(conn, flt, wi_set)
        )

    merged = _collect_entries_for_merge(
        conn, flt, wi_set, cap=100_000, kind_p=kind_p, role_p=role_p
    )
    merged.sort(key=cmp_to_key(_cmp_journal_desc))
    return len(merged)


def _per_table_cap(offset: int, limit: int) -> int:
    """Сколько строк брать из каждой таблицы перед merge (верхняя граница по памяти)."""
    return min(50000, max(500, offset + limit + 2000))


def api_journal_query(
    conn: sqlite3.Connection,
    flt: JournalFilters,
    *,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    limit = max(1, min(500, limit))
    offset = max(0, offset)

    wi_set = _wi_set(conn, flt)
    if flt.work_item_id is None and flt.root_id is None and flt.run_id is None:
        wi_set = None
    elif wi_set is not None and not wi_set:
        return {
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "items": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "order": "desc",
            "sort": "ts DESC, source_type_rank ASC, source_id DESC",
        }

    cap = _per_table_cap(offset, limit)
    kind_p = flt.normalized_kind()
    role_p = flt.normalized_role()

    merged = _collect_entries_for_merge(
        conn, flt, wi_set, cap=cap, kind_p=kind_p, role_p=role_p
    )
    merged.sort(key=cmp_to_key(_cmp_journal_desc))
    total = journal_count(conn, flt)
    page = merged[offset : offset + limit]
    page = enrich_journal_items(page)

    return {
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "items": page,
        "total": total,
        "limit": limit,
        "offset": offset,
        "order": "desc",
        "sort": "ts DESC, source_type_rank ASC, source_id DESC",
    }


def api_journal_for_work_item(
    conn: sqlite3.Connection, wi_id: str, **kwargs: Any
) -> dict[str, Any]:
    return api_journal_query(
        conn, JournalFilters(work_item_id=wi_id.strip()), **kwargs
    )


def api_journal_for_run(conn: sqlite3.Connection, run_id: str, **kwargs: Any) -> dict[str, Any]:
    return api_journal_query(conn, JournalFilters(run_id=run_id.strip()), **kwargs)


def api_journal_for_root(conn: sqlite3.Connection, root_id: str, **kwargs: Any) -> dict[str, Any]:
    return api_journal_query(conn, JournalFilters(root_id=root_id.strip()), **kwargs)
