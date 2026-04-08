from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Path as FastPath

from factory.logging import FactoryLogger
from factory.schemas import ImprovementReviewRequest

def list_improvements() -> dict[str, Any]:
    import factory.api_server as api_server

    conn = api_server._open_ro()
    try:
        try:
            rows = conn.execute(
                """
                SELECT id, source_type, source_ref, title, description, evidence,
                       fix_target, affected_role, priority_score, status, risk_level,
                       frequency, vision_id, created_at, reviewed_at, reviewed_by
                FROM improvement_candidates
                ORDER BY priority_score DESC, created_at DESC
                """
            ).fetchall()
        except sqlite3.OperationalError as e:
            api_server._LOG.debug("improvement_candidates table unavailable in list_improvements: %s", e)
            return {"candidates": [], "stats": {}}
        candidates = []
        for r in rows:
            candidates.append(
                {
                    "id": r["id"],
                    "source_type": r["source_type"],
                    "source_ref": r["source_ref"],
                    "title": r["title"],
                    "description": r["description"],
                    "evidence": r["evidence"],
                    "fix_target": r["fix_target"],
                    "affected_role": r["affected_role"],
                    "priority_score": float(r["priority_score"])
                    if r["priority_score"] is not None
                    else None,
                    "status": r["status"],
                    "risk_level": r["risk_level"],
                    "frequency": int(r["frequency"] or 0),
                    "vision_id": r["vision_id"],
                    "created_at": r["created_at"],
                    "reviewed_at": r["reviewed_at"],
                    "reviewed_by": r["reviewed_by"],
                }
            )
        st_rows = conn.execute(
            """
            SELECT status, COUNT(*) AS c FROM improvement_candidates GROUP BY status
            """
        ).fetchall()
        stats = {x["status"]: int(x["c"]) for x in st_rows}
        for k in ("proposed", "approved", "converted", "rejected", "expired"):
            stats.setdefault(k, 0)
        return {"candidates": candidates, "stats": stats}
    finally:
        conn.close()


def approve_improvement(
    ic_id: str = FastPath(..., min_length=1, max_length=128),
    body: ImprovementReviewRequest = Body(default=ImprovementReviewRequest()),
) -> dict[str, Any]:
    import factory.api_server as api_server

    reviewed_by = str(body.reviewed_by or "dashboard").strip() or "dashboard"
    conn = api_server._open_rw()
    try:
        row = conn.execute(
            "SELECT id, status FROM improvement_candidates WHERE id = ?", (ic_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        if row["status"] != "proposed":
            raise HTTPException(status_code=400, detail="only proposed can be approved")
        now = api_server._utc_now_iso()
        conn.execute(
            """
            UPDATE improvement_candidates
            SET status = 'approved', reviewed_at = ?, reviewed_by = ?
            WHERE id = ?
            """,
            (now, reviewed_by, ic_id),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


def reject_improvement(
    ic_id: str = FastPath(..., min_length=1, max_length=128),
) -> dict[str, Any]:
    import factory.api_server as api_server

    conn = api_server._open_rw()
    try:
        row = conn.execute(
            "SELECT id, status FROM improvement_candidates WHERE id = ?", (ic_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        if row["status"] != "proposed":
            raise HTTPException(status_code=400, detail="only proposed can be rejected")
        conn.execute(
            "UPDATE improvement_candidates SET status = 'rejected' WHERE id = ?",
            (ic_id,),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


def convert_improvement(
    ic_id: str = FastPath(..., min_length=1, max_length=128),
) -> dict[str, Any]:
    import factory.api_server as api_server
    from factory.factory_introspect import FactoryIntrospector

    conn = api_server._open_rw()
    try:
        logger = FactoryLogger(conn)
        intro = FactoryIntrospector()
        try:
            vid = intro.convert_one(conn, ic_id, logger)
        except ValueError as e:
            logger._py_logger.warning("convert_improvement failed for %s: %s", ic_id, e)
            raise HTTPException(status_code=400, detail="Invalid input for vision conversion") from e
        conn.commit()
        return {"ok": True, "vision_id": vid}
    finally:
        conn.close()


def build_router() -> APIRouter:
    from factory import deps as srv

    router = APIRouter(tags=["improvements"])
    router.add_api_route("/api/improvements", srv.list_improvements, methods=["GET"])
    router.add_api_route("/api/improvements/{ic_id}/approve", srv.approve_improvement, methods=["POST"])
    router.add_api_route("/api/improvements/{ic_id}/reject", srv.reject_improvement, methods=["POST"])
    router.add_api_route("/api/improvements/{ic_id}/convert", srv.convert_improvement, methods=["POST"])
    return router
