from __future__ import annotations

from fastapi import APIRouter


def build_router() -> APIRouter:
    from factory import deps as srv

    router = APIRouter(tags=["admin-health"])
    router.add_api_route("/health", srv.health, methods=["GET"])
    router.add_api_route("/api/health", srv.api_health, methods=["GET"])
    return router
