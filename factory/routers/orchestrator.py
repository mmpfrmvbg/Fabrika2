from __future__ import annotations

from fastapi import APIRouter


def build_router() -> APIRouter:
    from factory import deps as srv

    router = APIRouter(tags=["orchestrator"])
    router.add_api_route("/api/analytics", srv.api_analytics, methods=["GET"])
    router.add_api_route("/api/stats", srv.stats, methods=["GET"])
    router.add_api_route("/api/workers/status", srv.api_workers_status, methods=["GET"])
    router.add_api_route("/api/queue/forge_inbox", srv.queue_forge_inbox, methods=["GET"])
    router.add_api_route("/api/fsm/work_item", srv.fsm_work_item, methods=["GET"])
    router.add_api_route("/api/agents", srv.agents_list_compat, methods=["GET"])
    router.add_api_route("/api/failure-clusters", srv.failure_clusters, methods=["GET"])
    router.add_api_route("/api/failures", srv.failures, methods=["GET"])
    router.add_api_route("/api/hr", srv.hr_stub, methods=["GET"])
    router.add_api_route("/api/visions", srv.visions, methods=["GET"])
    router.add_api_route("/api/visions", srv.create_vision, methods=["POST"])
    router.add_api_route("/api/visions/{vision_id}/decompose", srv.decompose_vision_endpoint, methods=["POST"])
    return router
