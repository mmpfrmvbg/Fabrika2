from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from factory.dashboard_unified_journal import JournalFilters, api_journal_query
from factory.work_items_tree import build_work_items_tree


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


def tree() -> dict[str, Any]:
    import factory.api_server as api_server

    conn = api_server._open_ro()
    try:
        roots = build_work_items_tree(conn)
        return {"roots": roots}
    finally:
        conn.close()


def build_router() -> APIRouter:
    from factory import deps as srv

    router = APIRouter(tags=["journal"])
    router.add_api_route("/api/journal", srv.journal, methods=["GET"])
    router.add_api_route("/api/tree", srv.tree, methods=["GET"])
    return router
