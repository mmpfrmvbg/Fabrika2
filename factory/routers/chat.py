from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Path as FastPath, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from factory.chat_service import ChatService
from factory.config import AccountManager
from factory.logging import FactoryLogger
from factory.schemas import ChatCreateRequest


async def chat_qwen_create(request: Request) -> dict[str, str]:
    """
    Создать сессию чата с Qwen.
    Возвращает chat_id для подключения к SSE потоку.
    """
    import factory.api_server as api_server
    from factory.db import DB_PATH, init_db

    try:
        raw = await request.json()
    except Exception:
        api_server._LOG.exception("Invalid JSON in /api/chat/qwen request")
        raise HTTPException(status_code=400, detail="Invalid request body")
    try:
        payload = ChatCreateRequest.model_validate(raw)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors()) from e

    prompt = payload.prompt
    context = payload.context
    work_item_id = payload.work_item_id

    try:
        tmp = init_db(DB_PATH)
        tmp.close()
    except sqlite3.OperationalError as e:
        if "locked" not in str(e).lower():
            raise

    conn = api_server._open_rw()
    account_manager = AccountManager(conn, FactoryLogger(conn))
    service = ChatService(api_server._db_path(), account_manager)
    try:
        full_context = context or {}
        if work_item_id:
            work_item = conn.execute(
                "SELECT * FROM work_items WHERE id = ?",
                (work_item_id,),
            ).fetchone()
            if work_item:
                full_context.update(
                    {
                        "work_item_id": work_item_id,
                        "kind": work_item["kind"],
                        "title": work_item["title"],
                        "description": work_item["description"],
                        "status": work_item["status"],
                    }
                )

        chat_id = service.create_chat_session(prompt, full_context)
        return {"chat_id": chat_id}
    finally:
        service.close()
        conn.close()


async def chat_qwen_stream(
    chat_id: str = FastPath(..., min_length=1, max_length=128),
) -> StreamingResponse:
    """SSE поток для чата с Qwen."""
    import factory.api_server as api_server
    from factory.db import DB_PATH, init_db

    try:
        tmp = init_db(DB_PATH)
        tmp.close()
    except sqlite3.OperationalError as e:
        if "locked" not in str(e).lower():
            raise

    conn = api_server._open_rw()
    account_manager = AccountManager(conn, FactoryLogger(conn))
    service = ChatService(api_server._db_path(), account_manager)

    async def generate():
        try:
            async for chunk in service.stream_chat_response(chat_id):
                yield chunk
        finally:
            service.close()
            conn.close()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def build_router() -> APIRouter:
    from factory import deps as srv

    router = APIRouter(tags=["chat"])
    router.add_api_route("/api/chat/qwen", srv.chat_qwen_create, methods=["POST"])
    router.add_api_route("/api/chat/qwen/{chat_id}/stream", srv.chat_qwen_stream, methods=["GET"])
    return router
