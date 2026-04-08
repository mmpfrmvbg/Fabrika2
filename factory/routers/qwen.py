from __future__ import annotations

from fastapi import APIRouter


def build_router() -> APIRouter:
    from factory import api_server as srv

    router = APIRouter(tags=["qwen"])
    router.add_api_route("/api/qwen/fix", srv.qwen_fix_endpoint, methods=["POST"])
    return router
