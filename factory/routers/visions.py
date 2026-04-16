from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Path as FastPath, Request

from factory.agents.planner import decompose_with_planner
from factory.config import AccountManager
from factory.contracts.planner import PlannerInput
from factory.db import DB_PATH, get_connection
from factory.logging import FactoryLogger
from factory.models import EventType, Role
from factory.qwen_cli_runner import run_qwen_cli
from factory.schemas import VisionRequest
from factory.work_items import WorkItemOps
from factory.work_items_tree import subtree_for_root_id


async def _require_api_key_dep(request: Request) -> None:
    from factory.deps import require_api_key

    await require_api_key(request)


def visions() -> dict[str, Any]:
    conn = get_connection(DB_PATH, read_only=True)
    try:
        rows = conn.execute(
            "SELECT id, title, status, created_at FROM work_items WHERE kind = 'vision' ORDER BY created_at DESC"
        ).fetchall()
        items = []
        for r in rows:
            vid = r["id"]
            total_desc = conn.execute(
                "SELECT COUNT(*) AS c FROM work_items WHERE root_id = ? AND id != ?",
                (vid, vid),
            ).fetchone()["c"]
            done_desc = conn.execute(
                """
                SELECT COUNT(*) AS c FROM work_items
                WHERE root_id = ? AND id != ?
                  AND status IN ('done','cancelled','archived')
                """,
                (vid, vid),
            ).fetchone()["c"]
            atoms_total = conn.execute(
                """
                SELECT COUNT(*) AS c FROM work_items
                WHERE root_id = ? AND id != ? AND kind = 'atom'
                """,
                (vid, vid),
            ).fetchone()["c"]
            atoms_done = conn.execute(
                """
                SELECT COUNT(*) AS c FROM work_items
                WHERE root_id = ? AND id != ? AND kind = 'atom' AND status = 'done'
                """,
                (vid, vid),
            ).fetchone()["c"]
            pct = int(round((done_desc / total_desc) * 100)) if total_desc else 0
            atom_pct = int(round((atoms_done / atoms_total) * 100)) if atoms_total else 0
            items.append(
                {
                    "id": r["id"],
                    "title": r["title"],
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "progress": {
                        "total_descendants": int(total_desc),
                        "done_descendants": int(done_desc),
                        "pct": pct,
                        "atoms_total": int(atoms_total),
                        "atoms_done": int(atoms_done),
                        "atoms_pct": atom_pct,
                    },
                }
            )
        return {"items": items}
    finally:
        conn.close()


def create_vision(
    body: VisionRequest | dict[str, Any] = Body(...),
    _: None = Depends(_require_api_key_dep),
) -> dict[str, Any]:
    """
    Создаёт Vision и запускает planner (синхронно, MVP).
    Ответ: ``ok``, ``id``, ``title``, ``tree`` (один корень Vision с детьми), ``tree_stats``, ``reasoning``.
    """
    import factory.api_server as api_server

    payload = body if isinstance(body, VisionRequest) else VisionRequest.model_validate(body)
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail={"error": "title is required"})
    description = payload.description.strip() if payload.description is not None else None

    conn: sqlite3.Connection | None = None
    try:
        from factory.db import init_db  # lazy import

        try:
            tmp = init_db(DB_PATH)
            tmp.close()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                raise

        conn = get_connection(DB_PATH)
        logger = FactoryLogger(conn)
        ops = WorkItemOps(conn, logger)
        vision_id = ops.create_vision(title, description, auto_commit=False)
        logger.log(
            EventType.VISION_CREATED,
            "work_item",
            vision_id,
            "Vision created via API",
            work_item_id=vision_id,
            actor_role=Role.CREATOR.value,
            payload={"title": title, "description": description, "source": "api"},
            tags=["api", "vision"],
        )
        out = decompose_with_planner(
            conn=conn,
            logger=logger,
            inp=PlannerInput(
                work_item_id=vision_id,
                title=title,
                description=description or "",
                kind="vision",
                current_depth=0,
                max_depth=4,
            ),
        )

        def _stats(items: Any) -> dict[str, int]:
            c = {"epics": 0, "stories": 0, "tasks": 0, "atoms": 0}

            def walk(it: Any) -> None:
                k = it.kind
                if k == "epic":
                    c["epics"] += 1
                elif k == "story":
                    c["stories"] += 1
                elif k == "task":
                    c["tasks"] += 1
                elif k == "atom":
                    c["atoms"] += 1
                for ch in it.children:
                    walk(ch)

            for it in items:
                walk(it)
            return c

        stats = _stats(out.items)
        conn.commit()
        root_node = subtree_for_root_id(conn, vision_id)
        tree_payload: list[dict[str, Any]] = [root_node] if root_node else []
        return {
            "ok": True,
            "id": vision_id,
            "title": title,
            "tree": tree_payload,
            "tree_stats": stats,
            "reasoning": out.reasoning,
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception as e:
                logging.getLogger(__name__).debug("Failed to close vision creation DB connection: %s", e, exc_info=True)


def decompose_vision_endpoint(
    vision_id: str = FastPath(..., min_length=1, max_length=128),
    body: VisionRequest | dict[str, Any] = Body(...),
    _: None = Depends(_require_api_key_dep),
) -> dict[str, Any]:
    """
    Авто-декомпозиция Vision через Qwen.
    Возвращает иерархию: epics → stories → tasks → atoms.
    """
    import re

    payload = body if isinstance(body, VisionRequest) else VisionRequest.model_validate(body)
    title = payload.title.strip()
    description = (payload.description or "").strip()

    if not title:
        raise HTTPException(status_code=400, detail={"error": "title is required"})

    prompt = f"""
Декомпозируй задачу на иерархию Epic → Story → Task → Atom.

Vision: {title}
Описание: {description}

Верни ТОЛЬКО JSON без markdown:
{{
  "epics": [
    {{
      "title": "Epic title",
      "description": "Epic description",
      "stories": [
        {{
          "title": "Story title",
          "description": "Story description",
          "tasks": [
            {{
              "title": "Task title",
              "description": "Task description",
              "atoms": [
                {{
                  "title": "Atom title",
                  "description": "Atom description",
                  "files": ["path/to/file.py"]
                }}
              ]
            }}
          ]
        }}
      ]
    }}
  ]
}}
"""

    try:
        with get_connection(DB_PATH) as conn:
            logger = FactoryLogger(conn)
            am = AccountManager(conn, logger)
            result = run_qwen_cli(
                conn=conn,
                account_manager=am,
                logger=logger,
                work_item_id="api_decompose_preview",
                title=title,
                description=description,
                full_prompt=prompt,
            )
        result_text = result.stdout or result.stderr or ""
        json_match = re.search(r"\{.*\}", result_text, re.DOTALL)
        if json_match:
            hierarchy = json.loads(json_match.group())
        else:
            hierarchy = json.loads(result_text)
        return {"hierarchy": hierarchy, "ok": True}
    except json.JSONDecodeError:
        logging.getLogger(__name__).exception("Qwen decompose JSON error")
        raise HTTPException(status_code=500, detail={"error": "Invalid JSON from Qwen"})
    except Exception:
        logging.getLogger(__name__).exception("Qwen decompose error")
        raise HTTPException(status_code=500, detail={"error": "Decompose failed"})


def build_router() -> APIRouter:
    from factory import deps as srv

    router = APIRouter(tags=["visions"])
    router.add_api_route("/api/visions", srv.visions, methods=["GET"])
    router.add_api_route("/api/visions", srv.create_vision, methods=["POST"])
    router.add_api_route(
        "/api/visions/{vision_id}/decompose",
        srv.decompose_vision_endpoint,
        methods=["POST"],
    )
    return router
