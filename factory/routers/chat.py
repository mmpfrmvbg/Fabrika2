from __future__ import annotations

from fastapi import APIRouter


def build_router() -> APIRouter:
    from factory import deps as srv

    router = APIRouter(tags=["chat"])
    router.add_api_route("/api/chat/qwen", srv.chat_qwen_create, methods=["POST"])
    router.add_api_route("/api/chat/qwen/{chat_id}/stream", srv.chat_qwen_stream, methods=["GET"])
    return router
