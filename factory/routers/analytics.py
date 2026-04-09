from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from factory.analytics_api import compute_analytics
from factory.workers_status import workers_status_payload


def api_analytics(
    period: str = Query("24h", description="24h | 7d | 30d | all"),
) -> dict[str, Any]:
    """Метрики фабрики за период (read-only)."""
    p = (period or "24h").strip().lower()
    if p not in ("24h", "7d", "30d", "all"):
        raise HTTPException(
            status_code=400,
            detail="period must be one of: 24h, 7d, 30d, all",
        )
    import factory.api_server as api_server

    conn = api_server._open_ro()
    try:
        return compute_analytics(conn, p)
    finally:
        conn.close()


def stats() -> dict[str, Any]:
    import factory.api_server as api_server

    conn = api_server._open_ro()
    try:
        by_kind = {r["kind"]: r["c"] for r in conn.execute("SELECT kind, COUNT(*) AS c FROM work_items GROUP BY kind")}
        by_status = {
            r["status"]: r["c"] for r in conn.execute("SELECT status, COUNT(*) AS c FROM work_items GROUP BY status")
        }
        runs_total = conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"]
        last_ev = conn.execute("SELECT MAX(event_time) AS t FROM event_log").fetchone()["t"]
        wi_total = conn.execute("SELECT COUNT(*) AS c FROM work_items").fetchone()["c"]
        total_visions = conn.execute(
            "SELECT COUNT(*) AS c FROM work_items WHERE kind = 'vision'"
        ).fetchone()["c"]
        total_atoms = conn.execute(
            "SELECT COUNT(*) AS c FROM work_items WHERE kind = 'atom'"
        ).fetchone()["c"]
        total_forge_runs = conn.execute(
            "SELECT COUNT(*) AS c FROM runs WHERE role = 'forge'"
        ).fetchone()["c"]
        last_forge = conn.execute(
            "SELECT MAX(finished_at) AS t FROM runs WHERE role = 'forge' AND finished_at IS NOT NULL"
        ).fetchone()["t"]
        improvements_proposed = 0
        improvements_stats: dict[str, int] = {}
        try:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS c FROM improvement_candidates GROUP BY status
                """
            ).fetchall()
            improvements_stats = {r["status"]: int(r["c"]) for r in rows}
            improvements_proposed = int(improvements_stats.get("proposed", 0))
        except sqlite3.OperationalError as e:
            api_server._LOG.debug("improvement_candidates table unavailable in stats: %s", e)
        from factory.routers.orchestrator import _orchestrator_heartbeat_from_conn

        orch_hb = _orchestrator_heartbeat_from_conn(conn)
        try:
            wst = workers_status_payload(conn)
        except sqlite3.OperationalError as e:
            api_server._LOG.debug("workers_status_payload fallback due to sqlite operational error: %s", e)
            wst = {"active": 0, "workers": [], "leases_total": 0}
        return {
            "active_workers": int(wst.get("active") or 0),
            "worker_leases_total": int(wst.get("leases_total") or 0),
            "workers_snapshot": wst.get("workers") or [],
            "work_items_total": wi_total,
            "by_kind": by_kind,
            "by_status": by_status,
            "runs_total": runs_total,
            "last_event_time": last_ev,
            "total_visions": int(total_visions),
            "total_atoms": int(total_atoms),
            "total_forge_runs": int(total_forge_runs),
            "last_forge_run_at": last_forge,
            "improvements_proposed": improvements_proposed,
            "improvements_stats": improvements_stats,
            **orch_hb,
        }
    finally:
        conn.close()


def api_workers_status() -> dict[str, Any]:
    """Активные lease в очередях (внешние worker-процессы и оркестратор)."""
    import factory.api_server as api_server

    conn = api_server._open_ro()
    try:
        return workers_status_payload(conn)
    finally:
        conn.close()


def build_router() -> APIRouter:
    from factory import deps as srv

    router = APIRouter(tags=["analytics"])
    router.add_api_route("/api/analytics", srv.api_analytics, methods=["GET"])
    router.add_api_route("/api/stats", srv.stats, methods=["GET"])
    router.add_api_route("/api/workers/status", srv.api_workers_status, methods=["GET"])
    return router
