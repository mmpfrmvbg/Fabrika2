"""Контекст Vision / Epic / Atom для event_log (Судья, фильтры API)."""

from __future__ import annotations

import sqlite3


def resolve_task_context(
    conn: sqlite3.Connection, work_item_id: str | None
) -> dict[str, str | None]:
    """
    Возвращает vision_id, epic_id, atom_id для ``work_item_id`` (подъём по parent_id).

    ``vision_id`` — корень (root); при отсутствии в цепочке берётся ``work_items.root_id``.
    Один запрос (рекурсивный CTE) вместо N SELECT по предкам.
    """
    if not work_item_id:
        return {}
    rows = conn.execute(
        """
        WITH RECURSIVE anc(id, kind, parent_id, root_id) AS (
            SELECT id, kind, parent_id, root_id FROM work_items WHERE id = ?
            UNION ALL
            SELECT w.id, w.kind, w.parent_id, w.root_id
            FROM work_items w
            INNER JOIN anc ON w.id = anc.parent_id
        )
        SELECT id, kind, parent_id, root_id FROM anc
        """,
        (work_item_id,),
    ).fetchall()

    vision_id: str | None = None
    epic_id: str | None = None
    atom_id: str | None = None
    for r in rows:
        k = r["kind"]
        if k == "vision":
            vision_id = r["id"]
        elif k == "epic":
            epic_id = r["id"]
        elif k == "atom":
            atom_id = r["id"]
    if vision_id is None:
        r2 = conn.execute(
            "SELECT root_id FROM work_items WHERE id = ?", (work_item_id,)
        ).fetchone()
        if r2 and r2["root_id"]:
            vision_id = r2["root_id"]
    return {
        "vision_id": vision_id,
        "epic_id": epic_id,
        "atom_id": atom_id,
    }
