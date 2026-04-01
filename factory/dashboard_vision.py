"""GET/POST /api/visions — список и создание Vision (work_items.kind=vision) через WorkItemOps."""

from __future__ import annotations

import sqlite3

from .composition import wire
from .config import resolve_db_path


def api_visions_list(conn: sqlite3.Connection) -> dict:
    """
    Список верхнеуровневых Vision из work_items.
    Порядок: сначала новые (created_at DESC, затем id DESC для стабильности).
    """
    rows = conn.execute(
        """
        SELECT id, title, description, status, created_at, updated_at
        FROM work_items
        WHERE LOWER(kind) = 'vision'
        ORDER BY datetime(created_at) DESC, id DESC
        """
    ).fetchall()
    items = [
        {
            "id": r["id"],
            "title": r["title"],
            "description": r["description"],
            "status": r["status"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]
    return {"items": items}


def post_create_vision(title: str | None, description: str | None) -> tuple[bool, dict, int]:
    """
    Создаёт work_item kind=vision, status=draft (начальное состояние FSM).
    Поле «open» из продуктового языка соответствует draft до submit.
    """
    t = (title or "").strip()
    if not t:
        return False, {"ok": False, "error": "title is required"}, 400

    db_path = resolve_db_path()
    factory = wire(db_path)
    try:
        ops = factory["ops"]
        c = factory["conn"]
        vid = ops.create_vision(t, (description or "").strip() or None)
        row = c.execute(
            """
            SELECT id, title, description, status, created_at, updated_at
            FROM work_items WHERE id = ?
            """,
            (vid,),
        ).fetchone()
        if not row:
            return False, {"ok": False, "error": "vision row missing after insert"}, 500
        return (
            True,
            {
                "ok": True,
                "vision_id": vid,
                "id": vid,
                "title": row["title"],
                "description": row["description"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "kind": "vision",
            },
            201,
        )
    finally:
        factory["conn"].close()
