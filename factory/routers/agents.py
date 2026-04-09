from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from factory.dashboard_api import _agents
from factory.work_items_tree import build_work_items_tree


def tree() -> dict[str, Any]:
    import factory.api_server as api_server

    conn = api_server._open_ro()
    try:
        roots = build_work_items_tree(conn)
        return {"roots": roots}
    finally:
        conn.close()


def agents_list_compat() -> dict[str, Any]:
    import factory.api_server as api_server

    conn = api_server._open_ro()
    try:
        return _agents(conn)
    finally:
        conn.close()


def hr_stub() -> dict[str, Any]:
    return {"policies": [], "proposals": []}


def build_agents_router() -> APIRouter:
    from factory import deps as srv

    router = APIRouter(tags=["agents"])
    router.add_api_route("/api/tree", srv.tree, methods=["GET"])
    router.add_api_route("/api/agents", srv.agents_list_compat, methods=["GET"])
    router.add_api_route("/api/hr", srv.hr_stub, methods=["GET"])
    return router
