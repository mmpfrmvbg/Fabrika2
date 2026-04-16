from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Path as FastPath, Query, Request
from fastapi.responses import JSONResponse, Response

from factory.config import get_factory_api_key
from factory.composition import wire
from factory.dashboard_api_read import get_work_items_paginated
from factory.db import DB_PATH, _row, _rows, gen_id, get_connection
from factory.logging import FactoryLogger
from factory.models import EventType, Role, _EDITABLE_STATUSES
from factory.routers.runs import _serialize_runs
from factory.schemas import BulkArchiveRequest, WorkItemCreateRequest, WorkItemPatchRequest
from factory.work_item_api_ops import (
    archive_work_item_subtree,
    cancel_work_item_subtree,
    delete_work_item_subtree,
    list_done_vision_roots_ready_to_archive,
)
from factory.work_items_tree import build_work_items_tree


def _valid_id(value: str, field: str) -> str:
    v = value.strip()
    if not v:
        raise HTTPException(status_code=400, detail=f"{field} must be a non-empty string")
    return v


async def _require_api_key(request: Request) -> None:
    expected = get_factory_api_key()
    if not expected:
        raise RuntimeError(
            "FACTORY_API_KEY is not configured. Set FACTORY_API_KEY before starting the API server."
        )
    got = (request.headers.get("X-API-Key") or "").strip()
    if got != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _serialize_export_work_items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    items = conn.execute("SELECT * FROM work_items ORDER BY created_at ASC, id ASC").fetchall()
    out: list[dict[str, Any]] = []
    for wi in items:
        wi_id = wi["id"]
        runs = conn.execute(
            """
            SELECT id, role, run_type, status, started_at, finished_at, work_item_id,
                   correlation_id, error_summary, tokens_used, source_run_id, dry_run
            FROM runs WHERE work_item_id = ?
            ORDER BY started_at DESC
            """,
            (wi_id,),
        ).fetchall()
        events = conn.execute(
            """
            SELECT id, event_time, event_type, actor_role, severity, message, payload
            FROM event_log
            WHERE work_item_id = ?
            ORDER BY event_time DESC, id DESC
            """,
            (wi_id,),
        ).fetchall()
        out.append(
            {
                **_row(wi),
                "runs": _serialize_runs(runs),
                "events": _rows(events),
            }
        )
    return out


def _work_items_export_csv(items: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "id",
            "kind",
            "title",
            "status",
            "parent_id",
            "root_id",
            "planning_depth",
            "priority",
            "created_at",
            "updated_at",
            "runs_count",
            "events_count",
        ]
    )
    for wi in items:
        writer.writerow(
            [
                wi.get("id"),
                wi.get("kind"),
                wi.get("title"),
                wi.get("status"),
                wi.get("parent_id"),
                wi.get("root_id"),
                wi.get("planning_depth"),
                wi.get("priority"),
                wi.get("created_at"),
                wi.get("updated_at"),
                len(wi.get("runs") or []),
                len(wi.get("events") or []),
            ]
        )
    return buf.getvalue()


def list_work_items(
    status: str | None = None,
    parent_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    conn = get_connection(DB_PATH, read_only=True)
    try:
        return get_work_items_paginated(
            conn,
            limit=limit,
            offset=offset,
            filters={"status": status, "parent_id": parent_id},
        )
    finally:
        conn.close()


def export_work_items(
    format: str = Query("json", pattern="^(json|csv)$"),  # noqa: A002
) -> Response:
    """Export all work items with nested runs/events as a downloadable file."""
    conn = get_connection(DB_PATH, read_only=True)
    try:
        items = _serialize_export_work_items(conn)
    finally:
        conn.close()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if format == "csv":
        return Response(
            content=_work_items_export_csv(items),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="work-items-export-{ts}.csv"'},
        )

    return Response(
        content=json.dumps({"items": items, "total": len(items)}, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="work-items-export-{ts}.json"'},
    )


def work_items_tree_endpoint() -> dict[str, Any]:
    """Полное дерево задач (корни без parent_id). Должен быть объявлен до ``/api/work-items/{wi_id}``."""
    conn = get_connection(DB_PATH, read_only=True)
    try:
        tree = build_work_items_tree(conn)
        return {"tree": tree}
    finally:
        conn.close()


def post_work_item_cancel(
    wi_id: str = FastPath(..., min_length=1, max_length=128),
    _: None = Depends(_require_api_key),
) -> dict[str, Any]:
    """FSM creator_cancelled + каскад по поддереву (post-order)."""
    factory = wire(DB_PATH)
    conn: sqlite3.Connection = factory["conn"]
    sm = factory["sm"]
    logger: FactoryLogger = factory["logger"]
    try:
        logger.log(
            EventType.API_WORK_ITEM_CANCEL,
            "work_item",
            wi_id,
            "POST /api/work-items/…/cancel",
            work_item_id=wi_id,
            actor_role=Role.CREATOR.value,
            tags=["api", "cancel"],
        )
        conn.commit()
        n, err = cancel_work_item_subtree(sm, conn, wi_id, actor_role=Role.CREATOR.value)
        if err:
            if err == "work_item not found":
                raise HTTPException(status_code=404, detail=err)
            raise HTTPException(status_code=400, detail=err)
        return {"ok": True, "cancelled_count": n}
    finally:
        conn.close()


def post_work_item_archive(
    wi_id: str = FastPath(..., min_length=1, max_length=128),
    _: None = Depends(_require_api_key),
) -> dict[str, Any]:
    """FSM archive_sweep для done и всех done-потомков."""
    factory = wire(DB_PATH)
    conn = factory["conn"]
    sm = factory["sm"]
    logger: FactoryLogger = factory["logger"]
    try:
        logger.log(
            EventType.API_WORK_ITEM_ARCHIVE,
            "work_item",
            wi_id,
            "POST /api/work-items/…/archive",
            work_item_id=wi_id,
            actor_role=Role.CREATOR.value,
            tags=["api", "archive"],
        )
        conn.commit()
        n, err = archive_work_item_subtree(sm, conn, wi_id, actor_role=Role.CREATOR.value)
        if err:
            if err == "work_item not found":
                raise HTTPException(status_code=404, detail=err)
            raise HTTPException(status_code=400, detail=err)
        return {"ok": True, "archived_count": n}
    finally:
        conn.close()


def patch_work_item(
    wi_id: str = FastPath(..., min_length=1, max_length=128),
    body: WorkItemPatchRequest = Body(...),
    _: None = Depends(_require_api_key),
) -> dict[str, Any]:
    title = body.title
    description = body.description
    if title is None and description is None:
        raise HTTPException(status_code=400, detail="expected title and/or description")

    conn = get_connection(DB_PATH)
    logger = FactoryLogger(conn)
    try:
        row = conn.execute("SELECT * FROM work_items WHERE id = ?", (wi_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="work_item not found")
        st = row["status"]
        if st not in _EDITABLE_STATUSES:
            raise HTTPException(status_code=400, detail=f"edit not allowed for status {st}")
        if title is not None and description is not None:
            conn.execute(
                "UPDATE work_items SET title = ?, description = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ?",
                (str(title).strip() or row["title"], str(description), wi_id),
            )
        elif title is not None:
            conn.execute(
                "UPDATE work_items SET title = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ?",
                (str(title).strip() or row["title"], wi_id),
            )
        elif description is not None:
            conn.execute(
                "UPDATE work_items SET description = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') WHERE id = ?",
                (str(description), wi_id),
            )
        else:
            raise HTTPException(status_code=400, detail="nothing to update")
        logger.log(
            EventType.WORK_ITEM_UPDATED,
            "work_item",
            wi_id,
            "work_item.updated via PATCH",
            work_item_id=wi_id,
            actor_role=Role.CREATOR.value,
            payload={
                "title": title if title is not None else row["title"],
                "description": description if description is not None else row["description"],
            },
            tags=["api", "patch"],
        )
        conn.commit()
        upd = conn.execute("SELECT * FROM work_items WHERE id = ?", (wi_id,)).fetchone()
        return {"work_item": _row(upd)}
    finally:
        conn.close()


def delete_work_item_endpoint(
    wi_id: str = FastPath(..., min_length=1, max_length=128),
    _: None = Depends(_require_api_key),
) -> dict[str, Any]:
    conn = get_connection(DB_PATH)
    logger = FactoryLogger(conn)
    try:
        n, err = delete_work_item_subtree(conn, logger, wi_id)
        if err:
            if err == "work_item not found":
                raise HTTPException(status_code=404, detail=err)
            raise HTTPException(status_code=400, detail=err)
        conn.commit()
        return {"ok": True, "deleted_count": n}
    finally:
        conn.close()


def post_bulk_archive(
    body: BulkArchiveRequest = Body(default=BulkArchiveRequest()),
    _: None = Depends(_require_api_key),
) -> dict[str, Any]:
    """Архивирует несколько корней (обычно Vision в done)."""
    ids = body.ids
    filt = (body.filter or "").strip()
    factory = wire(DB_PATH)
    conn = factory["conn"]
    sm = factory["sm"]
    try:
        if filt == "all_done_visions":
            target_ids = list_done_vision_roots_ready_to_archive(conn)
        elif isinstance(ids, list) and ids:
            target_ids = [_valid_id(str(x), "ids[]") for x in ids]
        else:
            raise HTTPException(
                status_code=400,
                detail='body must contain ids: [...] or filter: "all_done_visions"',
            )
        total = 0
        errors: list[str] = []
        for vid in target_ids:
            n, err = archive_work_item_subtree(sm, conn, vid, actor_role=Role.CREATOR.value)
            if err:
                errors.append(f"{vid}: {err}")
            else:
                total += n
        return {
            "ok": not errors,
            "archived_count": total,
            "errors": errors,
            "processed_roots": len(target_ids),
        }
    finally:
        conn.close()


def post_work_item_run(
    wi_id: str = FastPath(..., min_length=1, max_length=128),
    _: None = Depends(_require_api_key),
) -> Any:
    """Запуск forge для атома (тот же путь, что POST /api/tasks/…/forge-run в dashboard_api)."""
    correlation_id = str(uuid4())
    from factory.dashboard_task_run import accept_dashboard_task_run

    ok, body, status = accept_dashboard_task_run(wi_id, correlation_id=correlation_id)
    if not ok:
        return JSONResponse(status_code=status, content=body)
    return {
        "started": True,
        "run_id": body.get("run_id"),
        "correlation_id": correlation_id,
        "ok": body.get("ok", True),
        "status": body.get("status", "started"),
        "message": body.get("message", "accepted"),
    }


def post_tasks_forge_run_compat(
    wi_id: str = FastPath(..., min_length=1, max_length=128),
    _: None = Depends(_require_api_key),
) -> dict[str, Any]:
    """Совместимость с factory-os.html (старый путь)."""
    return post_work_item_run(wi_id)


def get_work_item(wi_id: str = FastPath(..., min_length=1, max_length=128)) -> dict[str, Any]:
    conn = get_connection(DB_PATH, read_only=True)
    try:
        wi = conn.execute("SELECT * FROM work_items WHERE id = ?", (wi_id,)).fetchone()
        if not wi:
            raise HTTPException(status_code=404, detail="work_item not found")
        files = conn.execute(
            """
            SELECT path, intent, description, required
            FROM work_item_files
            WHERE work_item_id = ?
            ORDER BY path
            """,
            (wi_id,),
        ).fetchall()
        forge_attempts = conn.execute(
            "SELECT COUNT(*) AS c FROM runs WHERE work_item_id = ? AND role = 'forge'",
            (wi_id,),
        ).fetchone()["c"]
        review_rejections = conn.execute(
            "SELECT COUNT(*) AS c FROM review_results WHERE work_item_id = ? AND verdict != 'approved'",
            (wi_id,),
        ).fetchone()["c"]
        judge_rejections = conn.execute(
            "SELECT COUNT(*) AS c FROM judge_verdicts WHERE work_item_id = ? AND verdict != 'approved'",
            (wi_id,),
        ).fetchone()["c"]
        ch = conn.execute(
            "SELECT * FROM work_items WHERE parent_id = ? ORDER BY created_at",
            (wi_id,),
        ).fetchall()
        qlease = conn.execute(
            """
            SELECT queue_name, lease_owner, lease_until, attempts
            FROM work_item_queue WHERE work_item_id = ?
            """,
            (wi_id,),
        ).fetchone()
        wi_out = {
            **_row(wi),
            "files": _rows(files),
            "forge_attempts": int(forge_attempts),
            "review_rejections": int(review_rejections),
            "judge_rejections": int(judge_rejections),
        }
        if qlease:
            wi_out["queue_lease"] = _row(qlease)
        return {"work_item": wi_out, "children": _rows(ch)}
    finally:
        conn.close()


def get_task_bundle(wi_id: str = FastPath(..., min_length=1, max_length=128)) -> dict[str, Any]:
    """Совместимость с factory-os.html (openDetail)."""
    conn = get_connection(DB_PATH, read_only=True)
    try:
        wi = conn.execute("SELECT * FROM work_items WHERE id = ?", (wi_id,)).fetchone()
        if not wi:
            raise HTTPException(status_code=404, detail="work_item not found")
        runs = conn.execute(
            """
            SELECT id, role, run_type, status, started_at, finished_at, work_item_id, correlation_id,
                   error_summary, tokens_used, source_run_id, dry_run
            FROM runs WHERE work_item_id = ?
            ORDER BY started_at DESC
            """,
            (wi_id,),
        ).fetchall()
        files = conn.execute(
            """
            SELECT path, intent, description, required
            FROM work_item_files WHERE work_item_id = ?
            ORDER BY path
            """,
            (wi_id,),
        ).fetchall()
        ev = conn.execute(
            """
            SELECT id, event_time, event_type, actor_role, severity, message
            FROM event_log
            WHERE work_item_id = ?
            ORDER BY event_time DESC, id DESC
            LIMIT 10
            """,
            (wi_id,),
        ).fetchall()
        return {
            "work_item": {
                **_row(wi),
                "files": _rows(files),
                "event_log": _rows(ev),
            },
            "runs": _serialize_runs(runs),
            "comments": [],
        }
    finally:
        conn.close()


def create_work_item_legacy(
    body: WorkItemCreateRequest | dict[str, Any] = Body(...),
    _: None = Depends(_require_api_key),
) -> dict[str, Any]:
    payload = body if isinstance(body, WorkItemCreateRequest) else WorkItemCreateRequest.model_validate(body)
    conn = get_connection(DB_PATH)
    try:
        idempotency_key = payload.idempotency_key.strip() if payload.idempotency_key else None
        if idempotency_key:
            existing = conn.execute(
                "SELECT id FROM work_items WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                return JSONResponse(  # type: ignore[return-value]
                    status_code=409,
                    content={"error": "duplicate", "existing_id": existing["id"]},
                )

        wi_id = gen_id("wi")
        parent_id = payload.parent_id.strip() if payload.parent_id else None
        root_id = wi_id
        depth = 0
        if parent_id:
            parent = conn.execute(
                "SELECT root_id, planning_depth FROM work_items WHERE id = ?",
                (parent_id,),
            ).fetchone()
            if not parent:
                raise HTTPException(status_code=404, detail="parent not found")
            root_id = str(parent["root_id"])
            depth = int(parent["planning_depth"] or 0) + 1

        correlation_id = str(uuid4())
        conn.execute(
            """
            INSERT INTO work_items (
                id, parent_id, root_id, kind, title, description, status,
                creator_role, owner_role, planning_depth, priority, correlation_id,
                idempotency_key, deadline_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'draft', 'creator', 'creator', ?, ?, ?, ?, ?)
            """,
            (
                wi_id,
                parent_id,
                root_id,
                payload.kind.strip().lower(),
                payload.title.strip(),
                payload.description.strip() if payload.description else None,
                depth,
                int(payload.priority),
                correlation_id,
                idempotency_key,
                payload.deadline_at.astimezone(timezone.utc).isoformat() if payload.deadline_at else None,
            ),
        )
        conn.commit()
        wi = conn.execute("SELECT * FROM work_items WHERE id = ?", (wi_id,)).fetchone()
        return {"ok": True, "work_item": _row(wi)}
    except sqlite3.IntegrityError:
        if idempotency_key:
            existing = conn.execute(
                "SELECT id FROM work_items WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                return JSONResponse(  # type: ignore[return-value]
                    status_code=409,
                    content={"error": "duplicate", "existing_id": existing["id"]},
                )
        raise
    finally:
        conn.close()


def build_router() -> APIRouter:
    from factory import deps as srv

    router = APIRouter(tags=["work-items"])
    router.add_api_route("/api/work-items", srv.list_work_items, methods=["GET"])
    router.add_api_route("/api/export/work-items", srv.export_work_items, methods=["GET"])
    router.add_api_route("/api/work-items/tree", srv.work_items_tree_endpoint, methods=["GET"])
    router.add_api_route("/api/work-items/{wi_id}/cancel", srv.post_work_item_cancel, methods=["POST"])
    router.add_api_route("/api/work-items/{wi_id}/archive", srv.post_work_item_archive, methods=["POST"])
    router.add_api_route("/api/work-items/{wi_id}", srv.patch_work_item, methods=["PATCH"])
    router.add_api_route("/api/work-items/{wi_id}", srv.delete_work_item_endpoint, methods=["DELETE"])
    router.add_api_route("/api/bulk/archive", srv.post_bulk_archive, methods=["POST"])
    router.add_api_route("/api/work-items/{wi_id}/run", srv.post_work_item_run, methods=["POST"])
    router.add_api_route("/api/tasks/{wi_id}/forge-run", srv.post_tasks_forge_run_compat, methods=["POST"])
    router.add_api_route("/api/work-items/{wi_id}", srv.get_work_item, methods=["GET"])
    router.add_api_route("/api/tasks/{wi_id}", srv.get_task_bundle, methods=["GET"])
    router.add_api_route("/api/work_items", srv.create_work_item_legacy, methods=["POST"])
    return router
