"""
HTTP API для дашборда factory-os.html: чтение той же SQLite, что и оркестратор (GET в ``mode=ro``).

Запуск: ``python -m factory --dashboard`` — порт **8420** по умолчанию; или
``python -m factory --dashboard-api`` / ``--dash`` — порт **8333** по умолчанию.
БД: ``FACTORY_DB_PATH`` или ``FACTORY_DB``, иначе ``proekt/factory.db``.

Read-only JSON: ``GET /api/work-items`` (фильтры ``?kind=&status=``), ``GET /api/work-items/<id>``,
``GET /api/work-items/<id>/tree``, ``GET /api/runs`` (``?work_item_id=&status=``), ``GET /api/runs/<id>``
(сначала как ``runs.id``, иначе как ``work_item_id`` — все прогоны по задаче), ``GET /api/events``, ``GET /api/stats``.

GET ``/api/visions`` — список Vision.
GET ``/api/journal`` — единый операционный журнал (read-model: event_log + run_steps + file_changes + comments + …);
  параметры ``work_item_id``, ``run_id``, ``root_id``, ``kind``, ``role``, ``limit``, ``offset``.
GET ``/api/work-items/<id>/journal``, ``GET /api/runs/<id>/journal``, ``GET /api/root/<id>/journal`` — сужение ленты.
POST ``/api/tasks/<id>/run`` — ручной запуск атома (пишет в БД, см. ``dashboard_task_run``).
POST ``/api/visions`` — создание Vision (``kind=vision``, ``status=draft``), см. ``dashboard_vision``.
POST ``/api/tasks/<id>/children`` — дочерний epic|story|task|atom (``post_create_child``).

Без авторизации; CORS ``*`` для разработки.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .config import load_dotenv, resolve_db_path
from .db import resolve_effective_run_id
from .dashboard_api_read import (
    api_task_detail,
    api_task_events_chronological,
    api_tasks_list,
    api_tree_nested,
    api_work_items_list,
)
from .dashboard_task_actions import enrich_task_detail
from .dashboard_task_children import post_create_child
from .dashboard_task_comments import post_task_comment
from .dashboard_task_run import accept_dashboard_task_run
from .dashboard_task_transition import post_task_transition
from .dashboard_vision import post_create_vision
from .dashboard_live_read import (
    api_atom_log,
    api_atoms_list,
    api_forge_inbox_simple,
    api_stats_dashboard,
    api_vision_tree,
    api_visions_with_atom_counts,
    api_work_item_subtree,
)
from .dashboard_unified_journal import JournalFilters, api_journal_query

load_dotenv()
_LOG = logging.getLogger(__name__)


def _read_post_json(handler: BaseHTTPRequestHandler) -> dict:
    try:
        n = int(handler.headers.get("Content-Length", 0) or 0)
    except ValueError:
        n = 0
    if n <= 0:
        return {}
    raw = handler.rfile.read(n)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}

# Каноническая лестница фабрики: Vision → Story → Epic → Task → Atom
KIND_LABELS: dict[str, str] = {
    "vision": "Vision",
    "story": "Story",
    "epic": "Epic",
    "task": "Task",
    "atom": "Atom",
}
# Алиасы в БД → канонический kind для API/UI
_KIND_ALIASES: dict[str, str] = {
    "initiative": "story",
    "atm_change": "atom",
}


def _normalize_kind(db_kind: str | None) -> tuple[str, str]:
    k = (db_kind or "").strip().lower()
    if k in _KIND_ALIASES:
        k = _KIND_ALIASES[k]
    if k not in KIND_LABELS:
        k = "task"
    return k, KIND_LABELS[k]


def _canonical_level(kind: str) -> int:
    order = ("vision", "story", "epic", "task", "atom")
    try:
        return order.index(kind)
    except ValueError:
        return 0


def _last_event_per_work_item(conn: sqlite3.Connection) -> dict[str, dict]:
    """Последнее событие event_log по каждому work_item_id (по максимальному id)."""
    try:
        rows = conn.execute(
            """
            SELECT e.work_item_id, e.id, e.event_type, e.event_time, e.message, e.payload
            FROM event_log e
            INNER JOIN (
                SELECT work_item_id, MAX(id) AS mid
                FROM event_log
                WHERE work_item_id IS NOT NULL
                GROUP BY work_item_id
            ) t ON e.work_item_id = t.work_item_id AND e.id = t.mid
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        wid = r["work_item_id"]
        pl_raw = r["payload"]
        payload_obj = None
        if pl_raw:
            try:
                payload_obj = json.loads(pl_raw)
            except json.JSONDecodeError:
                payload_obj = None
        msg = (r["message"] or "").replace("\n", " ")
        out[wid] = {
            "id": r["id"],
            "event_type": r["event_type"],
            "event_time": r["event_time"],
            "message": msg,
            "payload": payload_obj,
        }
    return out


def _run_counts_by_work_item(conn: sqlite3.Connection) -> dict[str, int]:
    try:
        rows = conn.execute(
            """
            SELECT work_item_id, COUNT(*) AS c
            FROM runs
            WHERE work_item_id IS NOT NULL
            GROUP BY work_item_id
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r["work_item_id"]: int(r["c"]) for r in rows}


def _last_step_per_work_item(conn: sqlite3.Connection, wi_ids: list[str]) -> dict[str, dict]:
    """Последний по времени шаг run_steps среди всех прогонов задачи."""
    if not wi_ids:
        return {}
    ph = ",".join("?" * len(wi_ids))
    try:
        rows = conn.execute(
            f"""
            SELECT r.work_item_id, rs.step_kind, rs.summary, rs.status, rs.created_at
            FROM run_steps rs
            INNER JOIN runs r ON r.id = rs.run_id
            WHERE r.work_item_id IN ({ph})
            ORDER BY rs.created_at DESC
            """,
            wi_ids,
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        wid = r["work_item_id"]
        if wid in out:
            continue
        out[wid] = {
            "step_kind": r["step_kind"],
            "summary": (r["summary"] or "")[:500],
            "status": r["status"],
            "created_at": r["created_at"],
        }
    return out


def _latest_architect_comments(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        rows = conn.execute(
            """
            SELECT work_item_id, comment, created_at
            FROM architect_comments
            ORDER BY created_at DESC
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    out: dict[str, str] = {}
    for r in rows:
        wid = r["work_item_id"]
        if wid not in out:
            out[wid] = r["comment"] or ""
    return out


def _proekt_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _connect() -> sqlite3.Connection:
    from .db import get_connection

    p = resolve_db_path()
    return get_connection(p, read_only=True)


def _cors_headers(handler: BaseHTTPRequestHandler) -> None:
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "*")


def _json_response(handler: BaseHTTPRequestHandler, obj: object, status: int = 200) -> None:
    body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    _cors_headers(handler)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _text_response(handler: BaseHTTPRequestHandler, body: bytes, content_type: str) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    _cors_headers(handler)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _dashboard_public_origin(handler: BaseHTTPRequestHandler) -> str:
    """
    Публичный origin для fetch с той же машины, что открыла страницу.
    Предпочитаем Host из запроса (корректно при bind 0.0.0.0 и заходе по LAN-IP).
    """
    host_hdr = (handler.headers.get("Host") or "").strip()
    if host_hdr:
        h = host_hdr.split("/")[0].strip()
        if "@" in h:
            h = h.split("@")[-1]
        return f"http://{h}".rstrip("/")

    bind_host, port = handler.server.server_address
    if isinstance(bind_host, bytes):
        bind_host = bind_host.decode("utf-8", "replace")
    bind_s = str(bind_host)
    if bind_s in ("0.0.0.0", "::"):
        bind_s = "127.0.0.1"
    if ":" in bind_s and not bind_s.startswith("["):
        return f"http://[{bind_s}]:{port}".rstrip("/")
    return f"http://{bind_s}:{port}".rstrip("/")


def _factory_os_html_bytes(handler: BaseHTTPRequestHandler, html_path: Path) -> bytes:
    """Вставляет ранний script: data-api-base + FACTORY_API_BASE (без хардкода порта в статике)."""
    text = html_path.read_text(encoding="utf-8")
    base = _dashboard_public_origin(handler)
    base_json = json.dumps(base, ensure_ascii=False)
    inject = (
        '<script id="factory-dashboard-api-config" type="text/javascript">\n'
        f"(function(){{var b={base_json};\n"
        'document.documentElement.setAttribute("data-api-base", b);\n'
        'if (typeof window.FACTORY_API_BASE === "undefined" || window.FACTORY_API_BASE === "") {\n'
        "  window.FACTORY_API_BASE = b;\n"
        "}\n"
        "})();\n"
        "</script>\n"
    )
    if "<head>" in text:
        text = text.replace("<head>", "<head>\n" + inject, 1)
    else:
        text = inject + text
    return text.encode("utf-8")


def _events(
    conn: sqlite3.Connection,
    limit: int,
    offset: int,
    work_item_id: str | None = None,
    run_id: str | None = None,
    since_time: str | None = None,
    event_type_substr: str | None = None,
) -> dict:
    wheres: list[str] = []
    params: list = []
    if work_item_id:
        wheres.append(
            "(work_item_id = ? OR (entity_type = 'work_item' AND entity_id = ?) "
            "OR run_id IN (SELECT id FROM runs WHERE work_item_id = ?))"
        )
        params.extend([work_item_id, work_item_id, work_item_id])
    if run_id:
        wheres.append("(run_id = ? OR (entity_type = 'run' AND entity_id = ?))")
        params.extend([run_id, run_id])
    if since_time:
        wheres.append("event_time > ?")
        params.append(since_time)
    if event_type_substr:
        wheres.append("LOWER(event_type) LIKE LOWER(?)")
        params.append(f"%{event_type_substr.strip()}%")
    where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""

    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM event_log{where_sql}", params
    ).fetchone()["c"]
    rows = conn.execute(
        f"""
        SELECT id, event_time, event_type, severity, message, entity_type, entity_id,
               actor_role, run_id, work_item_id, payload
        FROM event_log
        {where_sql}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    ).fetchall()
    items = []
    for r in rows:
        msg = (r["message"] or "").replace("\n", " ")
        sev = (r["severity"] or "info").lower()
        if sev == "fatal":
            sev = "error"
        pl_raw = r["payload"]
        payload_obj = None
        if pl_raw:
            try:
                payload_obj = json.loads(pl_raw)
            except json.JSONDecodeError:
                payload_obj = None
        et = r["event_time"]
        ar = r["actor_role"] or "—"
        items.append(
            {
                "id": r["id"],
                "event_time": et,
                "created_at": et,
                "event_type": r["event_type"],
                "severity": sev,
                "actor_role": ar,
                "actor": ar,
                "message": msg,
                "entity_type": r["entity_type"],
                "entity_id": r["entity_id"],
                "run_id": r["run_id"],
                "work_item_id": r["work_item_id"],
                "payload": payload_obj,
            }
        )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _work_items_with_files(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, parent_id, root_id, kind, title, description, status,
               creator_role, owner_role, priority, retry_count, max_retries,
               planning_depth, created_at, updated_at
        FROM work_items
        ORDER BY created_at ASC
        """
    ).fetchall()
    files_rows = conn.execute(
        """
        SELECT work_item_id, path, intent, description, required
        FROM work_item_files
        ORDER BY path
        """
    ).fetchall()
    by_wi: dict[str, list[dict]] = {}
    for f in files_rows:
        wid = f["work_item_id"]
        by_wi.setdefault(wid, []).append(
            {
                "path": f["path"],
                "intent": f["intent"],
                "description": f["description"] or "",
            }
        )
    arch = _latest_architect_comments(conn)
    last_ev = _last_event_per_work_item(conn)
    run_cnt = _run_counts_by_work_item(conn)
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        wid = d["id"]
        d["files"] = by_wi.get(wid, [])
        raw_kind = d.get("kind")
        nk, lbl = _normalize_kind(raw_kind if isinstance(raw_kind, str) else None)
        d["kind"] = nk
        d["label"] = lbl
        d["level"] = _canonical_level(nk)
        d["architect_comment"] = arch.get(wid)
        # status — уже в work_items.status (FSM)
        d["status"] = d.get("status") or ""
        le = last_ev.get(wid)
        d["last_event"] = le
        d["run_count"] = run_cnt.get(wid, 0)
        out.append(d)
    wi_ids = [x["id"] for x in out]
    last_steps = _last_step_per_work_item(conn, wi_ids)
    for d in out:
        d["last_step"] = last_steps.get(d["id"])
    return out


def _task_tree(conn: sqlite3.Connection) -> dict:
    return {"work_items": _work_items_with_files(conn)}


def _work_items_single(conn: sqlite3.Connection, wi_id: str) -> dict:
    rows = _work_items_with_files(conn)
    for w in rows:
        if w["id"] == wi_id:
            return {"work_item": w}
    return {"work_item": None, "error": "not_found"}


def _forge_queue(conn: sqlite3.Connection) -> dict:
    """Очередь forge_inbox + метаданные; плюс work_items для совместимости с макетом."""
    qrows = conn.execute(
        """
        SELECT wiq.work_item_id, wiq.queue_name, wiq.priority, wiq.lease_owner,
               wiq.lease_until, wiq.available_at, wiq.attempts, wiq.max_attempts
        FROM work_item_queue wiq
        WHERE wiq.queue_name = 'forge_inbox'
        ORDER BY wiq.priority ASC, wiq.available_at ASC
        """
    ).fetchall()
    queue = [dict(x) for x in qrows]
    wis = _work_items_with_files(conn)
    by_id = {w["id"]: w for w in wis}
    # атомы в типичных forge-статусах для карточек
    forge_wi = [
        w
        for w in wis
        if w.get("kind") == "atom"
        and w.get("status")
        in ("ready_for_work", "in_progress", "in_review", "planned")
    ]
    return {"queue": queue, "work_items": forge_wi}


def _runs(
    conn: sqlite3.Connection,
    limit: int,
    work_item_id: str | None = None,
    status: str | None = None,
) -> dict:
    wheres: list[str] = []
    params: list = []
    if work_item_id:
        wheres.append("r.work_item_id = ?")
        params.append(work_item_id)
    if status:
        wheres.append("r.status = ?")
        params.append(status.strip())
    where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT r.id, r.work_item_id, r.agent_id, r.role, r.run_type, r.status,
               r.started_at, r.finished_at, r.git_branch, r.error_summary,
               r.source_run_id, r.dry_run,
               wi.title AS work_item_title,
               (SELECT COUNT(*) FROM file_changes fc WHERE fc.run_id = r.id) AS file_changes_count,
               (SELECT COUNT(*) FROM run_steps rs WHERE rs.run_id = r.id) AS run_steps_count
        FROM runs r
        LEFT JOIN work_items wi ON wi.id = r.work_item_id
        {where_sql}
        ORDER BY r.started_at DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    items = []
    for r in rows:
        items.append(
            {
                "id": r["id"],
                "run_id": r["id"],
                "work_item_id": r["work_item_id"],
                "agent_id": r["agent_id"],
                "role": r["role"],
                "run_type": r["run_type"],
                "status": r["status"],
                "started_at": r["started_at"],
                "finished_at": r["finished_at"],
                "git_branch": r["git_branch"],
                "error_summary": r["error_summary"],
                "source_run_id": r["source_run_id"],
                "dry_run": bool(r["dry_run"]),
                "work_item_title": r["work_item_title"],
                "file_changes_count": r["file_changes_count"],
                "run_steps_count": r["run_steps_count"],
            }
        )
    return {"items": items, "limit": limit}


def _run_record_with_steps(conn: sqlite3.Connection, r: sqlite3.Row) -> dict:
    """Один прогон из строки ``runs`` + ``run_steps`` + ``file_changes``."""
    rid = r["id"]
    effective_run_id = resolve_effective_run_id(conn, rid) or rid
    steps = conn.execute(
        """
        SELECT id, step_no, step_kind, status, summary, payload, duration_ms, created_at
        FROM run_steps
        WHERE run_id = ?
        ORDER BY step_no ASC
        """,
        (effective_run_id,),
    ).fetchall()
    fcs = conn.execute(
        """
        SELECT id, path, change_type, diff_summary, lines_added, lines_removed, created_at
        FROM file_changes
        WHERE run_id = ?
        ORDER BY path
        """,
        (effective_run_id,),
    ).fetchall()
    step_items: list[dict] = []
    for s in steps:
        pl = None
        raw_pl = s["payload"]
        if raw_pl:
            try:
                pl = json.loads(raw_pl)
            except json.JSONDecodeError:
                pl = None
        step_items.append(
            {
                "id": s["id"],
                "step_no": s["step_no"],
                "step_kind": s["step_kind"],
                "status": s["status"],
                "summary": s["summary"],
                "payload": pl,
                "duration_ms": s["duration_ms"],
                "created_at": s["created_at"],
            }
        )
    fc_items = [dict(x) for x in fcs]
    return {
        "id": r["id"],
        "work_item_id": r["work_item_id"],
        "agent_id": r["agent_id"],
        "role": r["role"],
        "run_type": r["run_type"],
        "status": r["status"],
        "started_at": r["started_at"],
        "finished_at": r["finished_at"],
        "git_branch": r["git_branch"],
        "error_summary": r["error_summary"],
        "source_run_id": r["source_run_id"],
        "dry_run": bool(r["dry_run"]),
        "effective_run_id": effective_run_id,
        "steps": step_items,
        "file_changes": fc_items,
    }


def _run_by_run_id(conn: sqlite3.Connection, run_id: str) -> dict | None:
    """GET /api/runs/<id> — если ``id`` есть в ``runs``, один прогон; иначе ``None``."""
    r = conn.execute(
        """
        SELECT r.id, r.work_item_id, r.agent_id, r.role, r.run_type, r.status,
               r.started_at, r.finished_at, r.git_branch, r.error_summary,
               r.source_run_id, r.dry_run
        FROM runs r
        WHERE r.id = ?
        """,
        (run_id,),
    ).fetchone()
    if not r:
        return None
    return {"run": _run_record_with_steps(conn, r)}


def _runs_detail(conn: sqlite3.Connection, work_item_id: str) -> dict:
    """Все прогоны по атому/задаче с шагами и file_changes (таблица ``runs``, не forge_runs)."""
    runs_rows = conn.execute(
        """
        SELECT r.id, r.work_item_id, r.agent_id, r.role, r.run_type, r.status,
               r.started_at, r.finished_at, r.git_branch, r.error_summary,
               r.source_run_id, r.dry_run
        FROM runs r
        WHERE r.work_item_id = ?
        ORDER BY r.started_at DESC
        """,
        (work_item_id,),
    ).fetchall()
    runs_out = [_run_record_with_steps(conn, r) for r in runs_rows]
    return {"work_item_id": work_item_id, "runs": runs_out}


def _run_effective(conn: sqlite3.Connection, run_id: str) -> dict | None:
    row = conn.execute("SELECT id FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        return None
    return {"effective_run_id": resolve_effective_run_id(conn, run_id) or run_id}


def _agents(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT id, role, model_name, prompt_version, active FROM agents ORDER BY role"
    ).fetchall()
    agents = []
    for r in rows:
        agents.append(
            {
                "id": r["id"],
                "role": r["role"],
                "model_name": r["model_name"] or "—",
                "prompt_version": r["prompt_version"] or "—",
                "active": bool(r["active"]),
                "runs_today": 0,
                "status": "active" if r["active"] else "idle",
            }
        )
    return {"agents": agents}


def _fsm_stub(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """
        SELECT from_state, event_name, to_state, guard_name, action_name
        FROM state_transitions
        WHERE entity_type = 'work_item'
        ORDER BY from_state, event_name
        """
    ).fetchall()
    transitions = []
    for r in rows:
        transitions.append(
            {
                "from_state": r["from_state"],
                "event_name": r["event_name"],
                "to_state": r["to_state"],
                "guard_name": r["guard_name"] or "",
                "action_name": r["action_name"] or "",
            }
        )
    return {"transitions": transitions}


def _judgements_stub(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """
        SELECT id, work_item_id, verdict, reason_code, explanation, created_at
        FROM (
            SELECT id, work_item_id, verdict, reason_code, explanation, created_at
            FROM decisions
            UNION ALL
            SELECT id, work_item_id, verdict,
                   COALESCE(rejection_reason_code, '') AS reason_code,
                   substr(payload_json, 1, 2000) AS explanation,
                   created_at
            FROM judge_verdicts
        )
        ORDER BY created_at DESC
        LIMIT 50
        """
    ).fetchall()
    items = []
    for r in rows:
        items.append(
            {
                "id": r["id"],
                "work_item_id": r["work_item_id"],
                "verdict": r["verdict"],
                "reason_code": r["reason_code"] or "",
                "event": "",
                "cluster_id": None,
                "summary": (r["explanation"] or "")[:500],
                "created_at": r["created_at"],
            }
        )
    return {"items": items}


class DashboardRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        _cors_headers(self)
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/api/visions":
            try:
                data = _read_post_json(self)
                title = data.get("title") if isinstance(data.get("title"), str) else None
                desc = data.get("description") if isinstance(data.get("description"), str) else None
                ok, payload, code = post_create_vision(title, desc)
            except (OSError, sqlite3.OperationalError) as e:
                _json_response(
                    self,
                    {"ok": False, "error": str(e), "db_path": str(resolve_db_path())},
                    503,
                )
                return
            except Exception as e:  # noqa: BLE001
                _json_response(self, {"ok": False, "error": str(e)}, 500)
                return
            _json_response(self, payload, code)
            return

        m_child = re.match(r"^/api/tasks/([^/]+)/children$", path)
        if m_child:
            pid = unquote(m_child.group(1))
            try:
                data = _read_post_json(self)
                ok, payload, code = post_create_child(pid, data)
            except (OSError, sqlite3.OperationalError) as e:
                _json_response(
                    self,
                    {"ok": False, "error": str(e), "db_path": str(resolve_db_path())},
                    503,
                )
                return
            except Exception as e:  # noqa: BLE001
                _json_response(self, {"ok": False, "error": str(e)}, 500)
                return
            _json_response(self, payload, code)
            return

        m_com = re.match(r"^/api/tasks/([^/]+)/comments$", path)
        if m_com:
            wi_id = unquote(m_com.group(1))
            try:
                data = _read_post_json(self)
                _ok, payload, code = post_task_comment(
                    wi_id,
                    data.get("author"),
                    data.get("body") if isinstance(data.get("body"), str) else "",
                )
            except (OSError, sqlite3.OperationalError) as e:
                _json_response(
                    self,
                    {"ok": False, "error": str(e), "db_path": str(resolve_db_path())},
                    503,
                )
                return
            except Exception as e:  # noqa: BLE001
                _json_response(self, {"ok": False, "error": str(e)}, 500)
                return
            _json_response(self, payload, code)
            return

        m_forge = re.match(r"^/api/tasks/([^/]+)/forge-run$", path)
        if m_forge:
            wi_f = unquote(m_forge.group(1))
            try:
                ok, payload, status = accept_dashboard_task_run(wi_f)
            except (OSError, sqlite3.OperationalError) as e:
                _json_response(
                    self,
                    {"ok": False, "error": str(e), "db_path": str(resolve_db_path())},
                    503,
                )
                return
            except Exception as e:  # noqa: BLE001
                _json_response(self, {"ok": False, "error": str(e)}, 500)
                return
            _json_response(self, payload, status)
            return

        m_tr = re.match(r"^/api/tasks/([^/]+)/transition$", path)
        if m_tr:
            wi_t = unquote(m_tr.group(1))
            try:
                data = _read_post_json(self)
                ok, payload, code = post_task_transition(wi_t, data)
            except (OSError, sqlite3.OperationalError) as e:
                _json_response(
                    self,
                    {"ok": False, "error": str(e), "db_path": str(resolve_db_path())},
                    503,
                )
                return
            except Exception as e:  # noqa: BLE001
                _json_response(self, {"ok": False, "error": str(e)}, 500)
                return
            _json_response(self, payload, code)
            return

        m_run = re.match(r"^/api/tasks/([^/]+)/run$", path)
        if not m_run:
            _json_response(self, {"ok": False, "error": "not found", "path": path}, 404)
            return
        wi_id = unquote(m_run.group(1))
        try:
            ok, payload, status = accept_dashboard_task_run(wi_id)
        except (OSError, sqlite3.OperationalError) as e:
            _json_response(
                self,
                {"ok": False, "error": str(e), "db_path": str(resolve_db_path())},
                503,
            )
            return
        except Exception as e:  # noqa: BLE001
            _json_response(self, {"ok": False, "error": str(e)}, 500)
            return
        _json_response(self, payload, status)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        proekt_root = Path(__file__).resolve().parent.parent
        html_path = proekt_root / "factory-os.html"

        if path in ("/", "/factory-os.html"):
            if not html_path.is_file():
                _json_response(self, {"error": "factory-os.html not found"}, 404)
                return
            _text_response(
                self,
                _factory_os_html_bytes(self, html_path),
                "text/html; charset=utf-8",
            )
            return

        try:
            conn = _connect()
        except (OSError, sqlite3.OperationalError) as e:
            _json_response(
                self,
                {
                    "error": str(e),
                    "db_path": str(resolve_db_path()),
                    "hint": "Создайте БД: запустите фабрику из proekt или задайте FACTORY_DB_PATH",
                },
                503,
            )
            return

        try:
            m_vtree = re.match(r"^/api/visions/([^/]+)/tree$", path)
            if m_vtree:
                vid_t = unquote(m_vtree.group(1))
                payload = api_vision_tree(conn, vid_t)
                if payload.get("error") == "not_found":
                    _json_response(self, payload, 404)
                elif payload.get("error") == "not_a_vision":
                    _json_response(self, payload, 400)
                else:
                    _json_response(self, payload)
                return
            m_alog = re.match(r"^/api/atoms/([^/]+)/log$", path)
            if m_alog:
                aid = unquote(m_alog.group(1))
                payload = api_atom_log(conn, aid)
                if payload.get("error") == "not_found":
                    _json_response(self, payload, 404)
                elif payload.get("error") == "not_an_atom":
                    _json_response(self, payload, 400)
                else:
                    _json_response(self, payload)
                return
            if path == "/api/atoms":
                st_f = (qs.get("status") or [""])[0].strip() or None
                _json_response(self, api_atoms_list(conn, st_f))
                return
            if path == "/api/queue/forge_inbox":
                _json_response(self, api_forge_inbox_simple(conn))
                return
            if path == "/api/stats":
                _json_response(self, api_stats_dashboard(conn))
                return
            m_wi_tree = re.match(r"^/api/work-items/([^/]+)/tree$", path)
            if m_wi_tree:
                w_sub = unquote(m_wi_tree.group(1))
                payload = api_work_item_subtree(conn, w_sub)
                if payload.get("error") == "not_found":
                    _json_response(self, payload, 404)
                else:
                    _json_response(self, payload)
                return
            m_wi_one = re.match(r"^/api/work-items/([^/]+)$", path)
            if m_wi_one:
                w_one = unquote(m_wi_one.group(1))
                detail_wi = api_task_detail(conn, w_one)
                if detail_wi.get("error") == "not_found":
                    _json_response(self, detail_wi, 404)
                else:
                    enrich_task_detail(detail_wi)
                    _json_response(self, detail_wi)
                return
            if path == "/api/work-items":
                kind_w = (qs.get("kind") or [""])[0].strip() or None
                st_w = (qs.get("status") or [""])[0].strip() or None
                _json_response(
                    self,
                    api_work_items_list(conn, kind=kind_w, status=st_w),
                )
                return
            m_wi_journal = re.match(r"^/api/work-items/([^/]+)/journal$", path)
            if m_wi_journal:
                wj = unquote(m_wi_journal.group(1))
                lim_j = min(500, max(1, int(qs.get("limit", ["100"])[0])))
                off_j = max(0, int(qs.get("offset", ["0"])[0]))
                kind_j = (qs.get("kind") or [""])[0].strip() or None
                role_j = (qs.get("role") or [""])[0].strip() or None
                _json_response(
                    self,
                    api_journal_query(
                        conn,
                        JournalFilters(
                            work_item_id=wj, kind=kind_j, role=role_j
                        ),
                        limit=lim_j,
                        offset=off_j,
                    ),
                )
                return
            if path == "/api/tree":
                _json_response(self, api_tree_nested(conn))
                return
            if path == "/api/visions":
                _json_response(self, api_visions_with_atom_counts(conn))
                return
            if path == "/api/tasks":
                kind_f = (qs.get("kind") or [""])[0].strip() or None
                st_f = (qs.get("status") or [""])[0].strip() or None
                par_raw = qs.get("parent_id")
                parent_f: str | None = None
                if par_raw:
                    p0 = (par_raw[0] or "").strip()
                    parent_f = p0 if p0 else ""
                _json_response(
                    self,
                    api_tasks_list(conn, kind=kind_f, status=st_f, parent_id=parent_f),
                )
                return
            m_ev = re.match(r"^/api/tasks/([^/]+)/events$", path)
            if m_ev:
                wid_e = unquote(m_ev.group(1))
                lim_ev = min(5000, max(1, int(qs.get("limit", ["2000"])[0])))
                _json_response(
                    self, api_task_events_chronological(conn, wid_e, limit=lim_ev)
                )
                return
            m_one = re.match(r"^/api/tasks/([^/]+)$", path)
            if m_one and m_one.group(1) != "tree":
                wid_o = unquote(m_one.group(1))
                detail = api_task_detail(conn, wid_o)
                if detail.get("error") == "not_found":
                    _json_response(self, detail, 404)
                else:
                    enrich_task_detail(detail)
                    _json_response(self, detail)
                return
            if path in ("/api/events",):
                limit = min(500, max(1, int(qs.get("limit", ["50"])[0])))
                offset = max(0, int(qs.get("offset", ["0"])[0]))
                wi_f = (qs.get("work_item_id") or [""])[0].strip() or None
                rn_f = (qs.get("run_id") or [""])[0].strip() or None
                since_f = (qs.get("since") or [""])[0].strip() or None
                et_f = (qs.get("event_type") or [""])[0].strip() or None
                _json_response(
                    self,
                    _events(conn, limit, offset, wi_f, rn_f, since_f, et_f),
                )
                return
            if path == "/api/journal":
                lim_u = min(500, max(1, int(qs.get("limit", ["100"])[0])))
                off_u = max(0, int(qs.get("offset", ["0"])[0]))
                wi_u = (qs.get("work_item_id") or [""])[0].strip() or None
                rn_u = (qs.get("run_id") or [""])[0].strip() or None
                root_u = (qs.get("root_id") or [""])[0].strip() or None
                kind_u = (qs.get("kind") or [""])[0].strip() or None
                role_u = (qs.get("role") or [""])[0].strip() or None
                _json_response(
                    self,
                    api_journal_query(
                        conn,
                        JournalFilters(
                            work_item_id=wi_u,
                            run_id=rn_u,
                            root_id=root_u,
                            kind=kind_u,
                            role=role_u,
                        ),
                        limit=lim_u,
                        offset=off_u,
                    ),
                )
                return
            m_root_journal = re.match(r"^/api/root/([^/]+)/journal$", path)
            if m_root_journal:
                rid_j = unquote(m_root_journal.group(1))
                lim_r = min(500, max(1, int(qs.get("limit", ["100"])[0])))
                off_r = max(0, int(qs.get("offset", ["0"])[0]))
                kind_r = (qs.get("kind") or [""])[0].strip() or None
                role_r = (qs.get("role") or [""])[0].strip() or None
                _json_response(
                    self,
                    api_journal_query(
                        conn,
                        JournalFilters(
                            root_id=rid_j, kind=kind_r, role=role_r
                        ),
                        limit=lim_r,
                        offset=off_r,
                    ),
                )
                return
            if path in ("/api/tasks/tree", "/api/task-tree", "/api/work_items"):
                wi_one = (qs.get("id") or [""])[0].strip() or None
                if wi_one:
                    _json_response(self, _work_items_single(conn, wi_one))
                    return
                _json_response(self, _task_tree(conn))
                return
            if path in ("/api/queue/forge", "/api/forge-queue"):
                _json_response(self, _forge_queue(conn))
                return
            m_run_journal = re.match(r"^/api/runs/([^/]+)/journal$", path)
            if m_run_journal:
                run_j = unquote(m_run_journal.group(1))
                lim_rr = min(500, max(1, int(qs.get("limit", ["100"])[0])))
                off_rr = max(0, int(qs.get("offset", ["0"])[0]))
                kind_rr = (qs.get("kind") or [""])[0].strip() or None
                role_rr = (qs.get("role") or [""])[0].strip() or None
                _json_response(
                    self,
                    api_journal_query(
                        conn,
                        JournalFilters(
                            run_id=run_j, kind=kind_rr, role=role_rr
                        ),
                        limit=lim_rr,
                        offset=off_rr,
                    ),
                )
                return
            m_run_effective = re.match(r"^/api/runs/([^/]+)/effective$", path)
            if m_run_effective:
                rid_eff = unquote(m_run_effective.group(1))
                eff = _run_effective(conn, rid_eff)
                if eff is None:
                    _json_response(self, {"error": "not_found", "run_id": rid_eff}, 404)
                    return
                _json_response(self, eff)
                return
            runs_rest = path[len("/api/runs/") :] if path.startswith("/api/runs/") else ""
            if runs_rest:
                rid_or_wi = unquote(runs_rest.strip("/"))
                if rid_or_wi:
                    one_run = _run_by_run_id(conn, rid_or_wi)
                    if one_run is not None:
                        _json_response(self, one_run)
                        return
                    _json_response(self, _runs_detail(conn, rid_or_wi))
                    return
            if path in ("/api/runs",):
                lim = min(200, max(1, int(qs.get("limit", ["50"])[0])))
                wi_r = (qs.get("work_item_id") or [""])[0].strip() or None
                st_r = (qs.get("status") or [""])[0].strip() or None
                _json_response(self, _runs(conn, lim, wi_r, st_r))
                return
            if path in ("/api/fsm/work_item",):
                _json_response(self, _fsm_stub(conn))
                return
            if path in ("/api/agents",):
                _json_response(self, _agents(conn))
                return
            if path in ("/api/judgements",):
                _json_response(self, _judgements_stub(conn))
                return
            if path in ("/api/failure-clusters", "/api/failures"):
                _json_response(self, {"clusters": [], "items": []})
                return
            if path in ("/api/verdicts", "/api/judge_verdicts"):
                payload = _judgements_stub(conn)
                _json_response(self, payload.get("items", []))
                return
            if path in ("/api/hr",):
                _json_response(self, {"policies": [], "proposals": []})
                return
            if path in ("/api/health",):
                _json_response(self, {"ok": True, "db": str(resolve_db_path())})
                return

            _json_response(self, {"error": "not found", "path": path}, 404)
        finally:
            conn.close()


def run_dashboard_api(host: str | None = None, port: int | None = None) -> None:
    host = host or os.environ.get("FACTORY_DASHBOARD_HOST", "127.0.0.1")
    port = port or int(os.environ.get("FACTORY_DASHBOARD_PORT", "8333"))
    server = HTTPServer((host, port), DashboardRequestHandler)
    _LOG.info("Factory dashboard API: http://%s:%s/", host, port)
    _LOG.info("  Open UI: http://%s:%s/factory-os.html", host, port)
    _LOG.info("  SQLite (read-only): %s", resolve_db_path())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _LOG.info("Stopped.")


if __name__ == "__main__":
    import argparse
    from pathlib import Path as _Path

    ap = argparse.ArgumentParser(description="Factory dashboard HTTP API (read-only GET + POST actions)")
    ap.add_argument(
        "--db",
        metavar="PATH",
        help="SQLite path (или задайте FACTORY_DB_PATH)",
    )
    ap.add_argument(
        "--host",
        default=None,
        help="Хост (по умолчанию FACTORY_DASHBOARD_HOST или 127.0.0.1)",
    )
    ap.add_argument(
        "--port",
        type=int,
        default=None,
        help="Порт (по умолчанию FACTORY_DASHBOARD_PORT или 8333)",
    )
    args = ap.parse_args()
    if args.db:
        os.environ["FACTORY_DB_PATH"] = str(_Path(args.db).resolve())
    load_dotenv()
    run_dashboard_api(host=args.host, port=args.port)
