"""POST /api/tasks/<parent_id>/children — дочерняя задача на уровень ниже родителя."""

from __future__ import annotations

from typing import Any

from .composition import wire
from .config import resolve_db_path
from .dashboard_api_read import _normalize_kind
from .models import EventType, Role, Severity
from .logging import FactoryLogger


# Иерархия как в seed_demo_vision: Vision → Epic → Story → Task → Atom
_PARENT_TO_CHILD: dict[str, str] = {
    "vision": "epic",
    "epic": "story",
    "story": "task",
    "task": "atom",
}

def _expected_child_kind(parent_canon: str) -> str | None:
    return _PARENT_TO_CHILD.get(parent_canon)


def _parse_files(raw: Any) -> list[dict]:
    if raw is None:
        return []
    if isinstance(raw, str):
        lines = [x.strip() for x in raw.replace(",", "\n").split("\n") if x.strip()]
        return [{"path": p, "intent": "modify"} for p in lines]
    if isinstance(raw, list):
        out = []
        for item in raw:
            if isinstance(item, str) and item.strip():
                out.append({"path": item.strip(), "intent": "modify"})
            elif isinstance(item, dict) and item.get("path"):
                out.append(
                    {
                        "path": str(item["path"]).strip(),
                        "intent": (item.get("intent") or "modify").strip(),
                    }
                )
        return out
    return []


def post_create_child(parent_id: str, body: dict) -> tuple[bool, dict, int]:
    title = (body.get("title") or "").strip() if isinstance(body.get("title"), str) else ""
    if not title:
        return False, {"ok": False, "error": "title is required"}, 400

    desc = body.get("description")
    description = (desc.strip() if isinstance(desc, str) else None) or None

    db_path = resolve_db_path()
    factory = wire(db_path)
    conn = factory["conn"]
    ops = factory["ops"]
    logger: FactoryLogger = factory["logger"]
    try:
        row = conn.execute(
            "SELECT id, kind, status FROM work_items WHERE id = ?",
            (parent_id,),
        ).fetchone()
        if not row:
            return False, {"ok": False, "error": "parent not found"}, 404

        pk, _ = _normalize_kind(row["kind"] if isinstance(row["kind"], str) else None)
        expected = _expected_child_kind(pk)
        if not expected:
            return False, {"ok": False, "error": "parent kind cannot have children (atom)"}, 400

        req_kind = body.get("kind")
        if isinstance(req_kind, str) and req_kind.strip():
            ck = req_kind.strip().lower()
            if ck == "initiative":
                ck = "story"
            if ck != expected:
                return (
                    False,
                    {
                        "ok": False,
                        "error": f"kind must be {expected} for this parent (got {ck})",
                    },
                    400,
                )
        child_kind = expected

        if child_kind == "atom":
            files = _parse_files(body.get("files"))
            if not files:
                return (
                    False,
                    {"ok": False, "error": "atom requires files (paths in work_item_files)"},
                    400,
                )
        else:
            files = _parse_files(body.get("files"))

        cid = ops.create_child(
            parent_id,
            child_kind,
            title,
            description,
            creator_role=Role.CREATOR.value,
            files=files if files else None,
            auto_commit=False,
        )
        logger.log(
            EventType.CHILD_CREATED,
            "work_item",
            cid,
            f"Дочерняя {child_kind} создана с дашборда (родитель {parent_id})",
            severity=Severity.INFO,
            work_item_id=cid,
            actor_role=Role.CREATOR.value,
            payload={
                "parent_id": parent_id,
                "child_id": cid,
                "kind": child_kind,
                "files_count": len(files),
            },
            tags=["dashboard", "child"],
        )
        conn.commit()
        return (
            True,
            {"ok": True, "id": cid, "work_item_id": cid, "kind": child_kind},
            201,
        )
    except ValueError as e:
        conn.rollback()
        return False, {"ok": False, "error": str(e)}, 400
    finally:
        conn.close()
