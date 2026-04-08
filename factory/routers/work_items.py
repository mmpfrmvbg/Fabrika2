from __future__ import annotations

from fastapi import APIRouter


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
