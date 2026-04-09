from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Query

from factory.db import DB_PATH, get_connection
from factory.dashboard_api import _fsm_stub
from factory.dashboard_live_read import api_forge_inbox_simple


def _load_judgements_items(
    conn: sqlite3.Connection, work_item_id: str | None, limit: int
) -> list[dict[str, Any]]:
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
        logging.getLogger(__name__).debug("judge_verdicts table unavailable while loading judgements: %s", e)
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
            logging.getLogger(__name__).debug("Failed to parse failed_guards_json for verdict %s: %s", r["id"], e)
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
        logging.getLogger(__name__).debug("review_results table unavailable while loading judgements: %s", e)
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
    conn = get_connection(DB_PATH, read_only=True)
    try:
        return {"items": _load_judgements_items(conn, work_item_id, limit)}
    finally:
        conn.close()


def queue_forge_inbox() -> dict[str, Any]:
    """Совместимость с factory-os.html (тот же контракт, что legacy ``dashboard_api``)."""
    conn = get_connection(DB_PATH, read_only=True)
    try:
        return api_forge_inbox_simple(conn)
    finally:
        conn.close()


def judge_verdicts(
    work_item_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Compatibility endpoint: always returns a JSON list for dashboard verdict pages."""
    conn = get_connection(DB_PATH, read_only=True)
    try:
        return _load_judgements_items(conn, work_item_id, limit)
    finally:
        conn.close()


def fsm_work_item() -> dict[str, Any]:
    conn = get_connection(DB_PATH, read_only=True)
    try:
        return _fsm_stub(conn)
    finally:
        conn.close()


def build_judgements_router() -> APIRouter:
    from factory import deps as srv

    router = APIRouter(tags=["judgements"])
    router.add_api_route("/api/judgements", srv.judgements, methods=["GET"])
    router.add_api_route("/api/verdicts", srv.judge_verdicts, methods=["GET"])
    router.add_api_route("/api/judge_verdicts", srv.judge_verdicts, methods=["GET"])
    router.add_api_route("/api/queue/forge_inbox", srv.queue_forge_inbox, methods=["GET"])
    router.add_api_route("/api/fsm/work_item", srv.fsm_work_item, methods=["GET"])
    return router
