from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Union
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Path as FastPath, Query, Request
from fastapi.responses import StreamingResponse

from factory.db import DB_PATH, gen_id, get_connection, resolve_effective_run_id
from factory.logging import FactoryLogger
from factory.models import EventType, Role
from factory.schemas import RunCreateRequest


async def _require_api_key(request: Request) -> None:
    import factory.api_server as api_server

    await api_server.require_api_key(request)


def _row(d: sqlite3.Row) -> dict[str, Any]:
    return {k: d[k] for k in d.keys()}


def _rows(rs: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [_row(r) for r in rs]


def _serialize_run_row(d: sqlite3.Row) -> dict[str, Any]:
    out = _row(d)
    out["source_run_id"] = out.get("source_run_id")
    out["dry_run"] = bool(out.get("dry_run"))
    return out


def _serialize_runs(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [_serialize_run_row(r) for r in rows]


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


def create_run(
    body: RunCreateRequest = Body(...),
    _: None = Depends(_require_api_key),
) -> Any:
    wi_id = body.work_item_id.strip()
    correlation_id = (body.correlation_id or "").strip() or str(uuid4())
    conn = get_connection(DB_PATH)
    try:
        wi = conn.execute("SELECT id FROM work_items WHERE id = ?", (wi_id,)).fetchone()
        if not wi:
            raise HTTPException(status_code=404, detail="work_item not found")
        run_id = gen_id("run")
        conn.execute("UPDATE work_items SET correlation_id = ? WHERE id = ?", (correlation_id, wi_id))
        conn.execute(
            """
            INSERT INTO runs (
                id, work_item_id, agent_id, role, run_type, status, correlation_id
            )
            VALUES (?, ?, 'agent_forge', 'forge', 'implement', 'queued', ?)
            """,
            (run_id, wi_id, correlation_id),
        )
        logger = FactoryLogger(conn)
        logger.log(
            EventType.RUN_STARTED,
            "run",
            run_id,
            "Run accepted via POST /api/runs",
            run_id=run_id,
            work_item_id=wi_id,
            actor_role=Role.CREATOR.value,
            payload={"source": "api.runs.create", "correlation_id": correlation_id},
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "ok": True,
        "work_item_id": wi_id,
        "run_id": run_id,
        "correlation_id": correlation_id,
        "status": "accepted",
    }


def runs_for_work_item(wi_id: str = FastPath(..., min_length=1, max_length=128)) -> dict[str, Any]:
    conn = get_connection(DB_PATH, read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT id, role, run_type, status, started_at, finished_at, correlation_id
                   , source_run_id, dry_run
            FROM runs WHERE work_item_id = ?
            ORDER BY started_at DESC
            """,
            (wi_id,),
        ).fetchall()
        return {"items": _serialize_runs(rows)}
    finally:
        conn.close()


def list_runs(
    work_item_id: str | None = None,
    limit: int = Query(120, ge=1, le=500),
) -> dict[str, Any]:
    conn = get_connection(DB_PATH, read_only=True)
    try:
        if work_item_id:
            rows = conn.execute(
                """
                SELECT id, role, run_type, status, started_at, finished_at, work_item_id, correlation_id
                       , source_run_id, dry_run
                FROM runs WHERE work_item_id = ?
                ORDER BY started_at DESC LIMIT ?
                """,
                (work_item_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, role, run_type, status, started_at, finished_at, work_item_id, correlation_id
                       , source_run_id, dry_run
                FROM runs ORDER BY started_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return {"items": _serialize_runs(rows)}
    finally:
        conn.close()


def get_run_detail(run_id: str = FastPath(..., min_length=1, max_length=128)) -> dict[str, Any]:
    conn = get_connection(DB_PATH, read_only=True)
    try:
        r = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if r:
            effective_run_id = resolve_effective_run_id(conn, run_id) or run_id
            steps = conn.execute(
                "SELECT * FROM run_steps WHERE run_id = ? ORDER BY step_no",
                (effective_run_id,),
            ).fetchall()
            fcs = conn.execute(
                "SELECT * FROM file_changes WHERE run_id = ? ORDER BY created_at",
                (effective_run_id,),
            ).fetchall()
            return {
                "run": {**_serialize_run_row(r), "effective_run_id": effective_run_id},
                "run_steps": _rows(steps),
                "file_changes": _rows(fcs),
            }
        rows = conn.execute(
            """
            SELECT * FROM runs WHERE work_item_id = ?
            ORDER BY started_at DESC
            """,
            (run_id,),
        ).fetchall()
        if rows:
            return {"runs": _serialize_runs(rows), "work_item_id": run_id}
        raise HTTPException(status_code=404, detail="run / work_item not found")
    finally:
        conn.close()


def get_run_steps(run_id: str = FastPath(..., min_length=1, max_length=128)) -> dict[str, Any]:
    conn = get_connection(DB_PATH, read_only=True)
    try:
        effective_run_id = resolve_effective_run_id(conn, run_id) or run_id
        steps = conn.execute(
            "SELECT * FROM run_steps WHERE run_id = ? ORDER BY step_no",
            (effective_run_id,),
        ).fetchall()
        if not steps:
            raise HTTPException(status_code=404, detail="no steps for this run id")
        return {"items": _rows(steps), "effective_run_id": effective_run_id}
    finally:
        conn.close()


def get_effective_run_id(run_id: str = FastPath(..., min_length=1, max_length=128)) -> dict[str, Any]:
    conn = get_connection(DB_PATH, read_only=True)
    try:
        r = conn.execute("SELECT id FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="run not found")
        return {"effective_run_id": resolve_effective_run_id(conn, run_id) or run_id}
    finally:
        conn.close()


def list_events(
    limit: int = Query(10, ge=1, le=500),
    work_item_id: str | None = None,
    event_type: str | None = None,
    stream: bool = False,
) -> Union[dict[str, Any], StreamingResponse]:
    conn = get_connection(DB_PATH, read_only=True)
    try:
        q = "SELECT * FROM event_log WHERE 1=1"
        params: list[Any] = []
        if work_item_id:
            q += " AND work_item_id = ?"
            params.append(work_item_id)
        if event_type:
            q += " AND event_type LIKE ?"
            params.append(f"%{event_type}%")
        q += " ORDER BY event_time DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        items = _rows(rows)
        if stream:

            def _event_stream() -> Any:
                for item in items:
                    yield f"event: {item.get('event_type', 'event')}\n"
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

            return StreamingResponse(_event_stream(), media_type="text/event-stream")
        return {"items": items, "limit": limit}
    finally:
        conn.close()


async def stream_events(
    request: Request,
    last_event_id: int = Query(default=0, ge=0),
    once: bool = Query(default=False),
) -> StreamingResponse:
    async def _event_stream() -> Any:
        cursor = int(last_event_id)
        while True:
            if await request.is_disconnected():
                break
            conn = get_connection(DB_PATH, read_only=True)
            try:
                rows = conn.execute(
                    """
                    SELECT id, event_type, payload
                    FROM event_log
                    WHERE id > ?
                    ORDER BY id ASC
                    """,
                    (cursor,),
                ).fetchall()
            finally:
                conn.close()

            if rows:
                for row in rows:
                    cursor = int(row["id"])
                    payload_raw = row["payload"]
                    payload_obj: dict[str, Any] = {}
                    if isinstance(payload_raw, str) and payload_raw.strip():
                        try:
                            parsed = json.loads(payload_raw)
                            if isinstance(parsed, dict):
                                payload_obj = parsed
                        except Exception:
                            payload_obj = {}
                    event_data = {
                        "id": cursor,
                        "type": row["event_type"],
                        "payload": payload_obj,
                    }
                    yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"
            else:
                yield ": keep-alive\n\n"
            if once:
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


def build_router() -> APIRouter:
    from factory import deps as srv

    router = APIRouter(tags=["runs"])
    router.add_api_route("/api/runs", srv.create_run, methods=["POST"])
    router.add_api_route("/api/work-items/{wi_id}/runs", srv.runs_for_work_item, methods=["GET"])
    router.add_api_route("/api/runs", srv.list_runs, methods=["GET"])
    router.add_api_route("/api/runs/{run_id}", srv.get_run_detail, methods=["GET"])
    router.add_api_route("/api/runs/{run_id}/steps", srv.get_run_steps, methods=["GET"])
    router.add_api_route("/api/runs/{run_id}/effective", srv.get_effective_run_id, methods=["GET"])
    router.add_api_route("/api/events", srv.stream_events, methods=["GET"])
    router.add_api_route("/api/events/list", srv.list_events, methods=["GET"], response_model=None)
    return router
