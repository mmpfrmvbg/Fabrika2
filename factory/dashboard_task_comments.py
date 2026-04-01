"""POST /api/tasks/<id>/comments — запись в ``comments`` + ``event_log``."""

from __future__ import annotations

from .composition import wire
from .config import resolve_db_path
from .models import CommentType, EventType, Role
from .dashboard_api_read import _normalize_kind


def post_task_comment(wi_id: str, author: str | None, body: str) -> tuple[bool, dict, int]:
    """
    ``author`` — роль (creator, planner, architect, …); по умолчанию creator.

    Возвращает ``(ok, json_body, http_status)``.
    """
    text = (body or "").strip()
    if not text:
        return False, {"ok": False, "error": "body is required"}, 400

    role = (author or Role.CREATOR.value).strip().lower()
    allowed = {r.value for r in Role}
    if role not in allowed:
        role = Role.CREATOR.value

    db_path = resolve_db_path()
    factory = wire(db_path)
    conn = factory["conn"]
    ops = factory["ops"]
    logger = factory["logger"]
    try:
        row = conn.execute(
            "SELECT id, kind FROM work_items WHERE id = ?",
            (wi_id,),
        ).fetchone()
        if not row:
            return False, {"ok": False, "error": "work_item not found"}, 404

        nk, _ = _normalize_kind(row["kind"] if isinstance(row["kind"], str) else None)
        if not nk:
            nk = "task"

        cmt_id = ops.add_comment(
            wi_id,
            role,
            text,
            comment_type=CommentType.NOTE.value,
            auto_commit=False,
        )
        logger.log(
            EventType.COMMENT_ADDED,
            "work_item",
            wi_id,
            f"Комментарий ({role}): {text[:120]}",
            work_item_id=wi_id,
            actor_role=role,
            payload={
                "comment_id": cmt_id,
                "author_role": role,
                "body_preview": text[:500],
                "kind": nk,
            },
            tags=["comment", "dashboard"],
        )
        conn.commit()
        return True, {"ok": True, "comment_id": cmt_id}, 201
    finally:
        conn.close()
