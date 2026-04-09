from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request

from factory.db import DB_PATH, get_connection
from factory.models import Role


def _queue_depths_from_conn(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT queue_name, COUNT(*) AS c
        FROM work_item_queue
        WHERE queue_name IN ('forge_inbox','review_inbox','judge_inbox')
        GROUP BY queue_name
        """
    ).fetchall()
    out = {r["queue_name"]: int(r["c"]) for r in rows}
    for k in ("forge_inbox", "review_inbox", "judge_inbox"):
        out.setdefault(k, 0)
    return out


def _parse_event_time_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    s = str(ts).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _orchestrator_heartbeat_from_conn(conn: sqlite3.Connection) -> dict[str, Any]:
    """Последнее событие с actor_role orchestrator (для UI heartbeat)."""
    row = conn.execute(
        """
        SELECT MAX(event_time) AS t FROM event_log
        WHERE LOWER(COALESCE(actor_role, '')) = ?
        """,
        (Role.ORCHESTRATOR.value,),
    ).fetchone()
    ts = row["t"] if row else None
    dt = _parse_event_time_iso(ts) if ts else None
    if not dt:
        return {
            "orchestrator_last_event_time": None,
            "orchestrator_seconds_since_last_event": None,
            "orchestrator_heartbeat_state": "none",
        }
    now = datetime.now(timezone.utc)
    sec = max(0.0, (now - dt).total_seconds())
    if sec < 30.0:
        state = "active"
    elif sec < 60.0:
        state = "warn"
    else:
        state = "stale"
    return {
        "orchestrator_last_event_time": ts,
        "orchestrator_seconds_since_last_event": sec,
        "orchestrator_heartbeat_state": state,
    }


def api_metrics() -> dict[str, Any]:
    from factory.api_server import _tick_interval_seconds

    conn = get_connection(DB_PATH, read_only=True)
    try:
        work_items_total = int(conn.execute("SELECT COUNT(*) AS c FROM work_items").fetchone()["c"])
        work_items_by_status = {
            r["status"]: int(r["c"])
            for r in conn.execute(
                "SELECT status, COUNT(*) AS c FROM work_items GROUP BY status"
            ).fetchall()
        }
        runs_total = int(conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"])
        runs_last_24h = int(
            conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM runs
                WHERE started_at IS NOT NULL
                  AND julianday(started_at) >= julianday('now', '-1 day')
                """
            ).fetchone()["c"]
        )
        failed_runs_last_24h = int(
            conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM runs
                WHERE started_at IS NOT NULL
                  AND julianday(started_at) >= julianday('now', '-1 day')
                  AND LOWER(COALESCE(status, '')) IN ('failed', 'error')
                """
            ).fetchone()["c"]
        )
        avg_run_duration_seconds = float(
            conn.execute(
                """
                SELECT COALESCE(AVG((julianday(finished_at) - julianday(started_at)) * 86400.0), 0.0) AS s
                FROM runs
                WHERE started_at IS NOT NULL
                  AND finished_at IS NOT NULL
                  AND julianday(finished_at) >= julianday(started_at)
                """
            ).fetchone()["s"]
            or 0.0
        )
        orchestrator_running = bool(
            conn.execute(
                """
                SELECT EXISTS(
                    SELECT 1
                    FROM event_log
                    WHERE LOWER(COALESCE(actor_role, '')) = 'orchestrator'
                      AND julianday(event_time) >= julianday('now', ?)
                ) AS is_running
                """,
                (f"-{2 * _tick_interval_seconds()} seconds",),
            ).fetchone()["is_running"]
        )
        return {
            "work_items_total": work_items_total,
            "work_items_by_status": work_items_by_status,
            "runs_total": runs_total,
            "runs_last_24h": runs_last_24h,
            "failed_runs_last_24h": failed_runs_last_24h,
            "avg_run_duration_seconds": avg_run_duration_seconds,
            "orchestrator_running": orchestrator_running,
        }
    finally:
        conn.close()


def orchestrator_status() -> dict[str, Any]:
    from factory.api_server import _orch_thread

    conn = get_connection(DB_PATH, read_only=True)
    try:
        qd = _queue_depths_from_conn(conn)
    finally:
        conn.close()
    return {
        "running": bool(_orch_thread.running),
        "last_tick": _orch_thread.last_tick,
        "ticks_total": int(_orch_thread.ticks_total),
        "items_processed": int(_orch_thread.items_processed_total),
        "last_tick_processed": dict(_orch_thread.last_tick_processed or {}),
        "queue_depths": qd,
    }


async def _require_api_key(request: Request) -> None:
    from factory.api_server import require_api_key

    await require_api_key(request)


def orchestrator_start(_: None = Depends(_require_api_key)) -> dict[str, Any]:
    from factory.api_server import _orch_thread

    _orch_thread.start()
    return orchestrator_status()


def orchestrator_stop(_: None = Depends(_require_api_key)) -> dict[str, Any]:
    from factory.api_server import _orch_thread

    _orch_thread.stop()
    return orchestrator_status()


def orchestrator_health() -> dict[str, Any]:
    """Heartbeat по event_log (actor_role=orchestrator), не путать с /api/orchestrator/status (поток tick)."""
    conn = get_connection(DB_PATH, read_only=True)
    try:
        h = _orchestrator_heartbeat_from_conn(conn)
        return {"ok": True, **h}
    finally:
        conn.close()


def orchestrator_tick(_: None = Depends(_require_api_key)) -> dict[str, Any]:
    from factory.api_server import _orch_thread

    processed = _orch_thread.tick_once()
    if processed:
        _orch_thread.items_processed_total += sum(processed.values())
    conn = get_connection(DB_PATH, read_only=True)
    try:
        qd = _queue_depths_from_conn(conn)
    finally:
        conn.close()
    return {
        "ok": True,
        "processed": processed,
        "queue_depths": qd,
        "status": {
            "running": bool(_orch_thread.running),
            "last_tick": _orch_thread.last_tick,
            "ticks_total": int(_orch_thread.ticks_total),
            "items_processed": int(_orch_thread.items_processed_total),
        },
    }


def build_router() -> APIRouter:
    from factory import deps as srv

    router = APIRouter(tags=["orchestrator"])
    router.add_api_route("/api/metrics", srv.api_metrics, methods=["GET"])
    router.add_api_route("/api/orchestrator/status", srv.orchestrator_status, methods=["GET"])
    router.add_api_route("/api/orchestrator/start", srv.orchestrator_start, methods=["POST"])
    router.add_api_route("/api/orchestrator/stop", srv.orchestrator_stop, methods=["POST"])
    router.add_api_route("/api/orchestrator/health", srv.orchestrator_health, methods=["GET"])
    router.add_api_route("/api/orchestrator/tick", srv.orchestrator_tick, methods=["POST"])
    return router
