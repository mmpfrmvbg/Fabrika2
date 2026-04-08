from __future__ import annotations

from fastapi import APIRouter


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
