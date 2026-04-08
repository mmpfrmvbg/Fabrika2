from __future__ import annotations

from fastapi import APIRouter


def build_router() -> APIRouter:
    from factory import api_server as srv

    router = APIRouter(tags=["admin-health"])
    router.add_api_route("/health", srv.health, methods=["GET"])
    router.add_api_route("/api/health", srv.api_health, methods=["GET"])
    router.add_api_route("/api/metrics", srv.api_metrics, methods=["GET"])
    router.add_api_route("/api/orchestrator/status", srv.orchestrator_status, methods=["GET"])
    router.add_api_route("/api/orchestrator/start", srv.orchestrator_start, methods=["POST"])
    router.add_api_route("/api/orchestrator/stop", srv.orchestrator_stop, methods=["POST"])
    router.add_api_route("/api/orchestrator/health", srv.orchestrator_health, methods=["GET"])
    router.add_api_route("/api/orchestrator/tick", srv.orchestrator_tick, methods=["POST"])
    return router
