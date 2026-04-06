"""MVP Планировщик: Vision → LLM → JSON → work_items + work_item_files."""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

import sqlite3

from .config import AccountManager
from .logging import FactoryLogger
from .models import Role
from .planner_prompt import build_planner_prompt
from .qwen_cli_runner import _env_qwen_dry_run, run_qwen_cli
from .work_items import WorkItemOps

_LOG = logging.getLogger(__name__)

_ALLOWED_INTENTS = frozenset({"create", "modify", "read", "delete", "rename"})


def extract_json_object(raw: str) -> dict[str, Any] | None:
    """Вырезает первый JSON-объект из ответа (в т.ч. после ```json ... ```)."""
    t = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", t, re.IGNORECASE)
    if fence:
        t = fence.group(1).strip()
    start = t.find("{")
    end = t.rfind("}")
    if start < 0 or end <= start:
        return None
    chunk = t[start : end + 1]
    try:
        out = json.loads(chunk)
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


def _norm_str(x: Any, *, label: str, path: str) -> str:
    if not isinstance(x, str) or not x.strip():
        raise ValueError(f"{path}: ожидалась непустая строка ({label})")
    return x.strip()


def _norm_file_entry(f: Any, path: str) -> dict[str, str | None]:
    if not isinstance(f, dict):
        raise ValueError(f"{path}: файл должен быть объектом")
    p = _norm_str(f.get("path"), label="path", path=f"{path}.path")
    action = (f.get("action") or "modify")
    if not isinstance(action, str):
        raise ValueError(f"{path}.action: строка")
    intent = action.strip().lower()
    if intent not in _ALLOWED_INTENTS:
        intent = "modify"
    details = f.get("details")
    desc = details.strip() if isinstance(details, str) and details.strip() else None
    return {"path": p, "intent": intent, "description": desc}


def validate_planner_payload(data: dict[str, Any]) -> None:
    epics = data.get("epics")
    if not isinstance(epics, list) or not epics:
        raise ValueError("Нужен непустой массив epics")
    for ei, epic in enumerate(epics):
        ep = f"epics[{ei}]"
        if not isinstance(epic, dict):
            raise ValueError(f"{ep}: объект")
        _norm_str(epic.get("title"), label="title", path=f"{ep}.title")
        stories = epic.get("stories")
        if not isinstance(stories, list) or not stories:
            raise ValueError(f"{ep}: непустой stories")
        for si, story in enumerate(stories):
            sp = f"{ep}.stories[{si}]"
            if not isinstance(story, dict):
                raise ValueError(f"{sp}: объект")
            _norm_str(story.get("title"), label="title", path=f"{sp}.title")
            tasks = story.get("tasks")
            if not isinstance(tasks, list) or not tasks:
                raise ValueError(f"{sp}: непустой tasks")
            for ti, task in enumerate(tasks):
                tp = f"{sp}.tasks[{ti}]"
                if not isinstance(task, dict):
                    raise ValueError(f"{tp}: объект")
                _norm_str(task.get("title"), label="title", path=f"{tp}.title")
                atoms = task.get("atoms")
                if not isinstance(atoms, list) or not atoms:
                    raise ValueError(f"{tp}: непустой atoms")
                for ai, atom in enumerate(atoms):
                    ap = f"{tp}.atoms[{ai}]"
                    if not isinstance(atom, dict):
                        raise ValueError(f"{ap}: объект")
                    _norm_str(atom.get("title"), label="title", path=f"{ap}.title")
                    files = atom.get("files")
                    if not isinstance(files, list) or not files:
                        raise ValueError(f"{ap}: непустой files")
                    for fi, fe in enumerate(files):
                        _norm_file_entry(fe, f"{ap}.files[{fi}]")


def _file_bracket(files: list[dict]) -> str:
    if not files:
        return ""
    first = files[0]
    p = first.get("path", "")
    intent = first.get("intent", "modify")
    return f" [{intent} {p}]"


def render_vision_tree(conn: sqlite3.Connection, vision_id: str) -> str:
    """Человекочитаемое дерево под Vision (unicode)."""
    v = conn.execute(
        "SELECT id, title FROM work_items WHERE id = ?", (vision_id,)
    ).fetchone()
    if not v:
        return f"(vision {vision_id!r} не найден)"
    # Без emoji: консоль Windows (cp1251) падает на U+1F4CB.
    lines: list[str] = [f"Vision: {v['title']}"]

    def children(pid: str) -> list[sqlite3.Row]:
        return list(
            conn.execute(
                """
                SELECT id, kind, title FROM work_items
                WHERE parent_id = ? ORDER BY created_at ASC, id ASC
                """,
                (pid,),
            ).fetchall()
        )

    def atom_files(wi_id: str) -> list[dict]:
        rows = conn.execute(
            "SELECT path, intent FROM work_item_files WHERE work_item_id = ? ORDER BY path",
            (wi_id,),
        ).fetchall()
        return [{"path": r["path"], "intent": r["intent"]} for r in rows]

    def walk(pid: str, depth: int) -> None:
        ch = children(pid)
        pad = "  " * depth
        for row in ch:
            kind = (row["kind"] or "").lower()
            label = kind[:1].upper() + kind[1:] if kind else "Item"
            extra = ""
            if kind == "atom":
                extra = _file_bracket(atom_files(row["id"]))
            lines.append(f"{pad}- {label}: {row['title']}{extra}")
            walk(row["id"], depth + 1)

    walk(vision_id, 1)
    return "\n".join(lines)


class Planner:
    def __init__(
        self,
        conn: sqlite3.Connection,
        logger: FactoryLogger,
        ops: WorkItemOps,
        account_manager: AccountManager,
    ):
        self.conn = conn
        self.logger = logger
        self.ops = ops
        self.account_manager = account_manager

    def decompose_vision(
        self,
        vision_id: str,
        *,
        creator_comments: Optional[str] = None,
        project_context: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Читает vision, вызывает LLM, парсит JSON, пишет дерево в одной транзакции.
        Возвращает summary с полями ok, epics, stories, tasks, atoms или ok=False и error.
        """
        conn = self.conn
        row = conn.execute(
            "SELECT id, kind, title, description FROM work_items WHERE id = ?",
            (vision_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": f"vision не найден: {vision_id}"}
        if (row["kind"] or "").lower() != "vision":
            return {"ok": False, "error": f"узел {vision_id} не vision (kind={row['kind']!r})"}

        prompt = build_planner_prompt(
            vision_title=row["title"] or "",
            vision_description=row["description"] or "",
            creator_comments=creator_comments,
            project_context=project_context,
        )
        tmp_dir: str | None = None
        try:
            tmp_dir = tempfile.mkdtemp(prefix="factory_planner_")
            fr = run_qwen_cli(
                conn=conn,
                account_manager=self.account_manager,
                logger=self.logger,
                work_item_id=vision_id,
                run_id=None,
                title="planner_decompose",
                description="",
                full_prompt=prompt,
                cwd=tmp_dir,
            )
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        if not fr.ok:
            msg = fr.error_message or fr.stderr or "Qwen CLI failed"
            _LOG.warning("planner LLM failed: %s", msg[:2000])
            return {"ok": False, "error": f"LLM: {msg[:1500]}"}

        combined = f"{fr.stdout or ''}\n{fr.stderr or ''}"

        data = extract_json_object(combined)
        if data is None:
            _LOG.warning("planner: ответ не JSON (первые 1200 символов):\n%s", combined[:1200])
            hint = ""
            if _env_qwen_dry_run():
                hint = " (для реального ответа задайте FACTORY_QWEN_DRY_RUN=0)"
            return {
                "ok": False,
                "error": f"LLM вернул не-JSON{hint}",
                "raw_preview": combined[:2000],
            }

        try:
            validate_planner_payload(data)
        except ValueError as e:
            _LOG.warning("planner: невалидная структура: %s", e)
            return {"ok": False, "error": f"JSON невалиден: {e}"}

        ne = ns = nt = na = 0
        try:
            for epic in data["epics"]:
                ne += 1
                eid = self.ops.create_child(
                    vision_id,
                    "epic",
                    epic["title"].strip(),
                    (epic.get("description") or "").strip() or None,
                    creator_role=Role.PLANNER.value,
                    auto_commit=False,
                )
                for story in epic["stories"]:
                    ns += 1
                    sid = self.ops.create_child(
                        eid,
                        "story",
                        story["title"].strip(),
                        (story.get("description") or "").strip() or None,
                        creator_role=Role.PLANNER.value,
                        auto_commit=False,
                    )
                    for task in story["tasks"]:
                        nt += 1
                        tid = self.ops.create_child(
                            sid,
                            "task",
                            task["title"].strip(),
                            (task.get("description") or "").strip() or None,
                            creator_role=Role.PLANNER.value,
                            auto_commit=False,
                        )
                        for atom in task["atoms"]:
                            na += 1
                            files_payload = []
                            for f in atom["files"]:
                                if isinstance(f, dict):
                                    fe = _norm_file_entry(f, "atom.files[]")
                                    files_payload.append(
                                        {
                                            "path": fe["path"],
                                            "intent": fe["intent"],
                                            "description": fe["description"],
                                        }
                                    )
                            # Атомы создаются в draft (create_child); готовность к кузнице — отдельно:
                            # forge_next_atom.mark_atom_ready_for_forge (ready_for_work + forge_inbox).
                            atom_id = self.ops.create_child(
                                tid,
                                "atom",
                                atom["title"].strip(),
                                (atom.get("description") or "").strip() or None,
                                creator_role=Role.PLANNER.value,
                                files=files_payload,
                                auto_commit=False,
                            )
                    if atom_id:
                        self.conn.execute(
                            "UPDATE work_items SET status='ready_for_work', owner_role='forge' WHERE id=?",
                            (atom_id,),
                        )
                        self.conn.execute(
                            """INSERT OR REPLACE INTO work_item_queue
                            (work_item_id, queue_name, priority, available_at, attempts)
                            VALUES (?, 'forge_inbox', 10, datetime('now'), 0)""",
                            (atom_id,),
                        )
            if na == 0:
                conn.rollback()
                _LOG.warning("planner: после валидации 0 атомов — откат")
                return {"ok": False, "error": "В дереве нет атомов"}
            conn.commit()
        except Exception as e:
            conn.rollback()
            _LOG.exception("planner: ошибка записи в БД")
            return {"ok": False, "error": str(e)}

        return {
            "ok": True,
            "epics": ne,
            "stories": ns,
            "tasks": nt,
            "atoms": na,
            "error": None,
        }


def run_plan_command(db_path: Path | None, vision_id: str) -> int:
    from .composition import wire

    factory = wire(db_path)
    conn = factory["conn"]
    vid = vision_id.strip()
    row = conn.execute("SELECT id, kind FROM work_items WHERE id = ?", (vid,)).fetchone()
    if not row:
        _LOG.error("Ошибка: vision не найден: %s", vid)
        return 1
    if (row["kind"] or "").lower() != "vision":
        _LOG.error("Ошибка: %s не vision (kind=%r)", vid, row["kind"])
        return 1

    planner = Planner(
        conn,
        factory["logger"],
        factory["ops"],
        factory["accounts"],
    )
    summary = planner.decompose_vision(vid)
    if not summary.get("ok"):
        err = summary.get("error") or "unknown"
        _LOG.error("%s", err)
        if summary.get("raw_preview"):
            _LOG.debug("--- raw preview ---")
            _LOG.debug("%s", summary["raw_preview"])
        return 1

    _LOG.info("%s", render_vision_tree(conn, vid))
    _LOG.info(
        f"\nИтого: {summary['epics']} epics, {summary['stories']} stories, "
        f"{summary['tasks']} tasks, {summary['atoms']} atoms"
    )
    return 0
