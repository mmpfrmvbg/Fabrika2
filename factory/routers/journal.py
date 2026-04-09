from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Query

from factory.dashboard_api import _fsm_stub
from factory.dashboard_live_read import api_forge_inbox_simple
from factory.dashboard_unified_journal import JournalFilters, api_journal_query


def journal(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    work_item_id: str | None = None,
    run_id: str | None = None,
    root_id: str | None = None,
    kind: str | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    import factory.api_server as api_server

    conn = api_server._open_ro()
    try:
        flt = JournalFilters(
            work_item_id=work_item_id,
            run_id=run_id,
            root_id=root_id,
            kind=kind,
            role=role,
        )
        return api_journal_query(conn, flt, limit=limit, offset=offset)
    finally:
        conn.close()


def _load_judgements_items(
    conn: sqlite3.Connection, work_item_id: str | None, limit: int
) -> list[dict[str, Any]]:
    import factory.api_server as api_server

    items: list[dict[str, Any]] = []
    qjv = """
        SELECT id, work_item_id, verdict, payload_json, failed_guards_json,
               rejection_reason_code, created_at, run_id
        FROM judge_verdicts
        WHERE 1=1
    """
    pjv: list[Any] = []
    if work_item_id:
        qjv += " AND work_item_id = ?"
        pjv.append(work_item_id)
    qjv += " ORDER BY created_at DESC LIMIT ?"
    pjv.append(limit)
    try:
        jv = conn.execute(qjv, pjv).fetchall()
    except sqlite3.OperationalError as e:
        api_server._LOG.debug("judge_verdicts table unavailable while loading judgements: %s", e)
        jv = []
    for r in jv:
        issues: Any = []
        p: dict[str, Any] = {}
        try:
            p = json.loads(r["payload_json"] or "{}")
            if isinstance(p, dict):
                issues = p.get("failed_guards") or p.get("issues") or []
            else:
                issues = []
        except json.JSONDecodeError:
            issues = []
        try:
            if r["failed_guards_json"]:
                issues = json.loads(r["failed_guards_json"])
        except (json.JSONDecodeError, TypeError) as e:
            api_server._LOG.debug("Failed to parse failed_guards_json for verdict %s: %s", r["id"], e)
        used_el = None
        if isinstance(p, dict):
            used_el = p.get("used_event_log")
        items.append(
            {
                "id": r["id"],
                "work_item_id": r["work_item_id"],
                "role": "judge",
                "verdict": r["verdict"],
                "reason_code": r["rejection_reason_code"] or "",
                "issues": issues if isinstance(issues, list) else [],
                "created_at": r["created_at"],
                "run_id": r["run_id"],
                "summary": (r["verdict"] or "")[:200],
                "used_event_log": used_el if isinstance(used_el, bool) else False,
            }
        )
    qrr = """
        SELECT id, work_item_id, verdict, issues_json, payload_json, created_at, reviewer_run_id
        FROM review_results
        WHERE 1=1
    """
    prr: list[Any] = []
    if work_item_id:
        qrr += " AND work_item_id = ?"
        prr.append(work_item_id)
    qrr += " ORDER BY created_at DESC LIMIT ?"
    prr.append(limit)
    try:
        rr = conn.execute(qrr, prr).fetchall()
    except sqlite3.OperationalError as e:
        api_server._LOG.debug("review_results table unavailable while loading judgements: %s", e)
        rr = []
    for r in rr:
        issues = []
        try:
            issues = json.loads(r["issues_json"] or "[]")
        except json.JSONDecodeError:
            issues = []
        items.append(
            {
                "id": r["id"],
                "work_item_id": r["work_item_id"],
                "role": "reviewer",
                "verdict": r["verdict"],
                "reason_code": "",
                "issues": issues,
                "created_at": r["created_at"],
                "run_id": r["reviewer_run_id"],
                "summary": (r["verdict"] or "")[:200],
            }
        )
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return items[:limit]


def judgements(
    work_item_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    import factory.api_server as api_server

    conn = api_server._open_ro()
    try:
        return {"items": _load_judgements_items(conn, work_item_id, limit)}
    finally:
        conn.close()


def judge_verdicts(
    work_item_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Compatibility endpoint: always returns a JSON list for dashboard verdict pages."""
    import factory.api_server as api_server

    conn = api_server._open_ro()
    try:
        return _load_judgements_items(conn, work_item_id, limit)
    finally:
        conn.close()


def queue_forge_inbox() -> dict[str, Any]:
    """Совместимость с factory-os.html (тот же контракт, что legacy ``dashboard_api``)."""
    import factory.api_server as api_server

    conn = api_server._open_ro()
    try:
        return api_forge_inbox_simple(conn)
    finally:
        conn.close()


def fsm_work_item() -> dict[str, Any]:
    import factory.api_server as api_server

    conn = api_server._open_ro()
    try:
        return _fsm_stub(conn)
    finally:
        conn.close()


def failure_clusters() -> dict[str, Any]:
    return {"clusters": [], "items": []}


def failures() -> dict[str, Any]:
    """Alias for /api/failure-clusters for frontend compatibility."""
    return {"clusters": [], "items": []}


def build_router() -> APIRouter:
    from factory import deps as srv

    router = APIRouter(tags=["journal"])
    router.add_api_route("/api/journal", srv.journal, methods=["GET"])
    router.add_api_route("/api/judgements", srv.judgements, methods=["GET"])
    router.add_api_route("/api/verdicts", srv.judge_verdicts, methods=["GET"])
    router.add_api_route("/api/judge_verdicts", srv.judge_verdicts, methods=["GET"])
    router.add_api_route("/api/queue/forge_inbox", srv.queue_forge_inbox, methods=["GET"])
    router.add_api_route("/api/fsm/work_item", srv.fsm_work_item, methods=["GET"])
    router.add_api_route("/api/failure-clusters", srv.failure_clusters, methods=["GET"])
    router.add_api_route("/api/failures", srv.failures, methods=["GET"])
    return router
