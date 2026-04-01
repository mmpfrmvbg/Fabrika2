"""CLI: создание Vision и Atom вручную (дашборд + forge_inbox)."""

from __future__ import annotations

from pathlib import Path

from .composition import wire
from .models import QueueName, Role


def run_create_vision(
    db_path: Path | None, title: str, description: str | None
) -> str:
    factory = wire(db_path)
    conn = factory["conn"]
    ops = factory["ops"]
    desc = (description or "").strip() or None
    vid = ops.create_vision(title.strip(), desc, auto_commit=False)
    conn.commit()
    return vid


def run_add_atom(
    db_path: Path | None,
    vision_id: str,
    title: str,
    description: str | None,
    *,
    files_csv: str | None,
    file_intent: str,
) -> str:
    factory = wire(db_path)
    conn = factory["conn"]
    ops = factory["ops"]
    sm = factory["sm"]

    vid = vision_id.strip()
    row = conn.execute(
        "SELECT id, kind FROM work_items WHERE id = ?",
        (vid,),
    ).fetchone()
    if not row:
        raise ValueError(f"Vision не найден: {vid}")
    if (row["kind"] or "").lower() != "vision":
        raise ValueError(f"Узел {vid} не vision (kind={row['kind']!r})")

    epic = conn.execute(
        """
        SELECT id FROM work_items
        WHERE parent_id = ? AND LOWER(kind) = 'epic'
        ORDER BY created_at ASC LIMIT 1
        """,
        (vid,),
    ).fetchone()
    if epic:
        epic_id = epic["id"]
    else:
        epic_id = ops.create_child(
            vid,
            "epic",
            "CLI backlog",
            "Epic для атомов, созданных из CLI",
            creator_role=Role.CREATOR.value,
            auto_commit=False,
        )

    paths = []
    if files_csv and files_csv.strip():
        paths = [p.strip() for p in files_csv.split(",") if p.strip()]
    if not paths:
        raise ValueError(
            "Нужен хотя бы один путь в --files (требование judge: объявленные файлы)"
        )
    intent = (file_intent or "modify").strip().lower()
    if intent not in ("modify", "create", "delete", "rename", "read"):
        intent = "modify"
    files = [{"path": p, "intent": intent, "description": None} for p in paths]

    desc = (description or "").strip() or None
    atom_id = ops.create_child(
        epic_id,
        "atom",
        title.strip(),
        desc,
        creator_role=Role.ARCHITECT.value,
        files=files,
        auto_commit=False,
    )

    ok, msg = sm.apply_transition(
        atom_id,
        "architect_submitted",
        actor_role=Role.ARCHITECT.value,
    )
    if not ok:
        conn.rollback()
        raise RuntimeError(f"architect_submitted: {msg}")

    ok, msg = sm.apply_transition(
        atom_id,
        "judge_approved",
        actor_role=Role.JUDGE.value,
    )
    if not ok:
        conn.rollback()
        raise RuntimeError(f"judge_approved: {msg}")

    # Высокий приоритет: иначе forge_worker с LIMIT 5 никогда не дойдёт до атома,
    # пока впереди в очереди висят задачи с неудачным dispatch (блокировки файлов).
    conn.execute(
        """
        UPDATE work_item_queue SET priority = 1
        WHERE work_item_id = ? AND queue_name = ?
        """,
        (atom_id, QueueName.FORGE_INBOX.value),
    )

    conn.commit()
    return atom_id
