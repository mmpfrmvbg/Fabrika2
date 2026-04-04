"""
Read-only HTTP API для дашборда (SQLite WAL, mode=ro).

Запуск:
  python -m factory.api_server
  python -m factory --dashboard
  FACTORY_DB=... FACTORY_API_PORT=8000 python -m factory.api_server

БД: ``FACTORY_DB`` / ``FACTORY_DB_PATH`` или ``proekt/factory.db`` (см. ``resolve_db_path``).
"""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import json
import logging
import os
import sqlite3
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import load_dotenv, resolve_db_path, AccountManager
from .composition import wire
from .dashboard_api import _agents, _fsm_stub
from .dashboard_live_read import api_forge_inbox_simple
from .dashboard_unified_journal import JournalFilters, api_journal_query
from .analytics_api import compute_analytics
from .workers_status import workers_status_payload
from .work_items_tree import build_work_items_tree, subtree_for_root_id
from .db import ensure_schema, gen_id, get_connection, resolve_effective_run_id
from .logging import FactoryLogger
from .models import EventType, Role
from .work_items import WorkItemOps
from .work_item_api_ops import (
    archive_work_item_subtree,
    cancel_work_item_subtree,
    delete_work_item_subtree,
    list_done_vision_roots_ready_to_archive,
)
from .agents.planner import decompose_with_planner
from .contracts.planner import PlannerInput
from .qwen_cli_runner import run_qwen_cli
from .chat_service import ChatService

load_dotenv()

# Глобальный logger для endpoint (создаётся при первом использовании)
_logger: FactoryLogger | None = None
_LOG = logging.getLogger("factory.api_server")

def _get_logger(conn: sqlite3.Connection | None = None) -> FactoryLogger:
    """Получить logger для endpoint."""
    global _logger
    if _logger is None:
        try:
            tmp_conn = get_connection(_db_path())
            _logger = FactoryLogger(tmp_conn)
        except Exception as e:
            # Fallback: logger без connection
            _LOG.debug("Falling back to FactoryLogger(None): %s", e, exc_info=True)
            _logger = FactoryLogger(None)
    return _logger


async def require_api_key(request: Request) -> None:
    """Если задан ``FACTORY_API_KEY``, мутирующие эндпоинты требуют заголовок ``X-API-Key``."""
    expected = (os.environ.get("FACTORY_API_KEY") or "").strip()
    if not expected:
        return
    got = (request.headers.get("X-API-Key") or "").strip()
    if got != expected:
        raise HTTPException(status_code=403, detail="Invalid API key")

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tick_interval_seconds() -> float:
    raw = os.environ.get("FACTORY_TICK_INTERVAL", "").strip()
    if not raw:
        return 3.0
    try:
        v = float(raw)
        return 0.2 if v < 0.2 else v
    except ValueError:
        return 3.0


class _OrchestratorThread:
    """
    Фоновый цикл оркестратора для api_server.

    Важно: создаёт СВОЁ SQLite-соединение (wire/init_db) и не переиспользует FastAPI-коннекты.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self.running = False
        self.last_tick: str | None = None
        self.ticks_total = 0
        self.items_processed_total = 0
        self.last_tick_processed: dict[str, int] = {}

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                self.running = True
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                daemon=True,
                name="factory-orchestrator-tick",
            )
            self.running = True
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self.running = False
            self._stop.set()
            t = self._thread
        if t and t.is_alive():
            t.join(timeout=5.0)

    def _run_loop(self) -> None:
        interval = _tick_interval_seconds()
        factory = None
        conn: sqlite3.Connection | None = None
        try:
            # отдельный граф/соединение для потока (retry на startup при lock contention)
            max_retries = 5
            last_err: Exception | None = None
            for attempt in range(max_retries):
                try:
                    factory = wire(_db_path())
                    conn = factory["conn"]
                    break
                except sqlite3.OperationalError as e:
                    last_err = e
                    if "locked" not in str(e).lower():
                        raise
                    if attempt >= max_retries - 1:
                        break
                    wait = min(2 ** (attempt + 1), 16)
                    print(
                        f"[orchestrator] tick thread connect retry {attempt + 1}/{max_retries}, "
                        f"wait {wait}s: {e}",
                        flush=True,
                    )
                    time.sleep(wait)
            if conn is None:
                raise RuntimeError(
                    f"Failed to start orchestrator thread (db locked): {last_err}"
                )
            print(
                f"[orchestrator] tick thread started interval={interval}s db={_db_path()}",
                flush=True,
            )
            while not self._stop.is_set():
                try:
                    processed = self.tick_once(_factory=factory)
                    if processed:
                        self.items_processed_total += sum(processed.values())
                        parts = ", ".join(
                            f"{k}:{processed.get(k, 0)}" for k in ("forge", "review", "judge")
                        )
                        print(f"[tick {self.ticks_total}] {parts}", flush=True)
                except sqlite3.OperationalError as e:
                    msg = str(e).lower()
                    if "locked" not in msg:
                        raise
                    # lock contention: подождать и продолжить
                    time.sleep(5.0)
                    continue
                time.sleep(interval)
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc()
            print(f"[orchestrator] tick thread crashed: {e}\n{tb}", flush=True)
            with self._lock:
                self.running = False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception as e:
                    _LOG.debug("Failed to close orchestrator thread db connection: %s", e, exc_info=True)

    def tick_once(self, *, _factory: dict | None = None) -> dict[str, int]:
        """
        Выполняет один tick и возвращает сколько задач dequeued по очередям.
        """
        with self._lock:
            self.ticks_total += 1
        factory = _factory or wire(_db_path())
        conn: sqlite3.Connection = factory["conn"]
        orch = factory["orchestrator"]

        last_id = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS m FROM event_log"
        ).fetchone()["m"]
        orch.tick()

        rows = conn.execute(
            """
            SELECT event_type FROM event_log
            WHERE id > ?
              AND event_type IN (?, ?, ?)
            """,
            (
                last_id,
                EventType.FORGE_STARTED.value,
                EventType.REVIEW_STARTED.value,
                EventType.JUDGE_STARTED.value,
            ),
        ).fetchall()

        forge_n = 0
        review_n = 0
        judge_n = 0
        for r in rows:
            et = r["event_type"]
            if et == EventType.FORGE_STARTED.value:
                forge_n += 1
            elif et == EventType.REVIEW_STARTED.value:
                review_n += 1
            elif et == EventType.JUDGE_STARTED.value:
                judge_n += 1

        mapped = {
            "forge": forge_n,
            "review": review_n,
            "judge": judge_n,
        }
        self.last_tick = _utc_now_iso()
        self.last_tick_processed = mapped

        if _factory is None:
            try:
                conn.close()
            except Exception as e:
                _LOG.debug("Failed to close temporary tick connection: %s", e, exc_info=True)
        return {k: v for k, v in mapped.items() if v}


_orch_thread = _OrchestratorThread()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _orch_thread.start()
    try:
        yield
    finally:
        _orch_thread.stop()


app = FastAPI(title="Factory read-only API", version="1.0", lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    _LOG.exception("Unhandled API exception for %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "detail": str(exc) or exc.__class__.__name__,
            "path": request.url.path,
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _db_path() -> Path:
    raw = os.environ.get("FACTORY_DB") or os.environ.get("FACTORY_DB_PATH")
    return resolve_db_path(Path(raw)) if raw else resolve_db_path()


def _open_ro() -> sqlite3.Connection:
    path = _db_path()
    if not path.exists():
        raise HTTPException(status_code=503, detail=f"Database not found: {path}")
    try:
        return get_connection(path, read_only=True)
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail=f"Database not found: {path}") from None


def _open_rw() -> sqlite3.Connection:
    """
    RW-соединение для минимальных write-операций дашборда (создание Vision).
    В отличие от `_open_ro` не включает `query_only`.
    """
    path = _db_path()
    ensure_schema(path)
    return get_connection(path)


def _row(d: sqlite3.Row) -> dict[str, Any]:
    return {k: d[k] for k in d.keys()}


def _rows(rs: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [_row(r) for r in rs]


def _serialize_run_row(d: sqlite3.Row) -> dict[str, Any]:
    out = _row(d)
    out["source_run_id"] = out.get("source_run_id")
    out["dry_run"] = bool(out.get("dry_run"))
    return out


def _serialize_runs(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [_serialize_run_row(r) for r in rows]


def _queue_depths_from_conn(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT queue_name, COUNT(*) AS c
        FROM work_item_queue
        WHERE queue_name IN ('forge_inbox','review_inbox','judge_inbox')
        GROUP BY queue_name
        """
    ).fetchall()
    out = {r["queue_name"]: int(r["c"]) for r in rows}
    for k in ("forge_inbox", "review_inbox", "judge_inbox"):
        out.setdefault(k, 0)
    return out


def _parse_event_time_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    s = str(ts).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _orchestrator_heartbeat_from_conn(conn: sqlite3.Connection) -> dict[str, Any]:
    """Последнее событие с actor_role orchestrator (для UI heartbeat)."""
    row = conn.execute(
        """
        SELECT MAX(event_time) AS t FROM event_log
        WHERE LOWER(COALESCE(actor_role, '')) = ?
        """,
        (Role.ORCHESTRATOR.value,),
    ).fetchone()
    ts = row["t"] if row else None
    dt = _parse_event_time_iso(ts) if ts else None
    if not dt:
        return {
            "orchestrator_last_event_time": None,
            "orchestrator_seconds_since_last_event": None,
            "orchestrator_heartbeat_state": "none",
        }
    now = datetime.now(timezone.utc)
    sec = max(0.0, (now - dt).total_seconds())
    if sec < 30.0:
        state = "active"
    elif sec < 60.0:
        state = "warn"
    else:
        state = "stale"
    return {
        "orchestrator_last_event_time": ts,
        "orchestrator_seconds_since_last_event": sec,
        "orchestrator_heartbeat_state": state,
    }


@app.get("/api/orchestrator/status")
def orchestrator_status() -> dict[str, Any]:
    conn = _open_ro()
    try:
        qd = _queue_depths_from_conn(conn)
    finally:
        conn.close()
    return {
        "running": bool(_orch_thread.running),
        "last_tick": _orch_thread.last_tick,
        "ticks_total": int(_orch_thread.ticks_total),
        "items_processed": int(_orch_thread.items_processed_total),
        "last_tick_processed": dict(_orch_thread.last_tick_processed or {}),
        "queue_depths": qd,
    }


@app.post("/api/orchestrator/start")
def orchestrator_start(_: None = Depends(require_api_key)) -> dict[str, Any]:
    _orch_thread.start()
    return orchestrator_status()


@app.post("/api/orchestrator/stop")
def orchestrator_stop(_: None = Depends(require_api_key)) -> dict[str, Any]:
    _orch_thread.stop()
    return orchestrator_status()


@app.get("/api/orchestrator/health")
def orchestrator_health() -> dict[str, Any]:
    """Heartbeat по event_log (actor_role=orchestrator), не путать с /api/orchestrator/status (поток tick)."""
    conn = _open_ro()
    try:
        h = _orchestrator_heartbeat_from_conn(conn)
        return {"ok": True, **h}
    finally:
        conn.close()


@app.post("/api/orchestrator/tick")
def orchestrator_tick(_: None = Depends(require_api_key)) -> dict[str, Any]:
    processed = _orch_thread.tick_once()
    conn = _open_ro()
    try:
        qd = _queue_depths_from_conn(conn)
    finally:
        conn.close()
    return {
        "ok": True,
        "processed": processed,
        "queue_depths": qd,
        "status": {
            "running": bool(_orch_thread.running),
            "last_tick": _orch_thread.last_tick,
            "ticks_total": int(_orch_thread.ticks_total),
            "items_processed": int(_orch_thread.items_processed_total),
        },
    }


@app.get("/api/work-items")
def list_work_items(
    status: str | None = None,
    parent_id: str | None = None,
) -> dict[str, Any]:
    conn = _open_ro()
    try:
        q = (
            "SELECT id, kind, parent_id, title, status, created_at FROM work_items WHERE 1=1"
        )
        params: list[Any] = []
        if status:
            q += " AND status = ?"
            params.append(status)
        if parent_id is not None:
            q += " AND parent_id = ?"
            params.append(parent_id if parent_id != "" else None)
        q += " ORDER BY created_at DESC"
        cur = conn.execute(q, params)
        return {"items": _rows(cur.fetchall())}
    finally:
        conn.close()


@app.get("/api/work-items/tree")
def work_items_tree_endpoint() -> dict[str, Any]:
    """Полное дерево задач (корни без parent_id). Должен быть объявлен до ``/api/work-items/{wi_id}``."""
    conn = _open_ro()
    try:
        tree = build_work_items_tree(conn)
        return {"tree": tree}
    finally:
        conn.close()


_EDITABLE_STATUSES = frozenset(
    {"draft", "planned", "ready_for_judge", "judge_rejected"}
)


@app.post("/api/work-items/{wi_id}/cancel")
def post_work_item_cancel(
    wi_id: str, _: None = Depends(require_api_key)
) -> dict[str, Any]:
    """FSM creator_cancelled + каскад по поддереву (post-order)."""
    factory = wire(_db_path())
    conn: sqlite3.Connection = factory["conn"]
    sm = factory["sm"]
    logger: FactoryLogger = factory["logger"]
    try:
        logger.log(
            EventType.API_WORK_ITEM_CANCEL,
            "work_item",
            wi_id,
            "POST /api/work-items/…/cancel",
            work_item_id=wi_id,
            actor_role=Role.CREATOR.value,
            tags=["api", "cancel"],
        )
        conn.commit()
        n, err = cancel_work_item_subtree(sm, conn, wi_id, actor_role=Role.CREATOR.value)
        if err:
            if err == "work_item not found":
                raise HTTPException(status_code=404, detail=err)
            raise HTTPException(status_code=400, detail=err)
        return {"ok": True, "cancelled_count": n}
    finally:
        conn.close()


@app.post("/api/work-items/{wi_id}/archive")
def post_work_item_archive(
    wi_id: str, _: None = Depends(require_api_key)
) -> dict[str, Any]:
    """FSM archive_sweep для done и всех done-потомков."""
    factory = wire(_db_path())
    conn = factory["conn"]
    sm = factory["sm"]
    logger: FactoryLogger = factory["logger"]
    try:
        logger.log(
            EventType.API_WORK_ITEM_ARCHIVE,
            "work_item",
            wi_id,
            "POST /api/work-items/…/archive",
            work_item_id=wi_id,
            actor_role=Role.CREATOR.value,
            tags=["api", "archive"],
        )
        conn.commit()
        n, err = archive_work_item_subtree(sm, conn, wi_id, actor_role=Role.CREATOR.value)
        if err:
            if err == "work_item not found":
                raise HTTPException(status_code=404, detail=err)
            raise HTTPException(status_code=400, detail=err)
        return {"ok": True, "archived_count": n}
    finally:
        conn.close()


@app.patch("/api/work-items/{wi_id}")
def patch_work_item(
    wi_id: str,
    body: dict[str, Any] = Body(...),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    title = body.get("title")
    description = body.get("description")
    if title is None and description is None:
        raise HTTPException(
            status_code=400, detail="expected title and/or description"
        )
    conn = _open_rw()
    logger = FactoryLogger(conn)
    try:
        row = conn.execute(
            "SELECT * FROM work_items WHERE id = ?", (wi_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="work_item not found")
        st = row["status"]
        if st not in _EDITABLE_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"edit not allowed for status {st}",
            )
        sets: list[str] = []
        params: list[Any] = []
        if title is not None:
            sets.append("title = ?")
            params.append(str(title).strip() or row["title"])
        if description is not None:
            sets.append("description = ?")
            params.append(
                None if description is None else str(description)
            )
        if not sets:
            raise HTTPException(status_code=400, detail="nothing to update")
        params.append(wi_id)
        conn.execute(
            f"UPDATE work_items SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        logger.log(
            EventType.WORK_ITEM_UPDATED,
            "work_item",
            wi_id,
            "work_item.updated via PATCH",
            work_item_id=wi_id,
            actor_role=Role.CREATOR.value,
            payload={
                "title": title if title is not None else row["title"],
                "description": description
                if description is not None
                else row["description"],
            },
            tags=["api", "patch"],
        )
        conn.commit()
        upd = conn.execute("SELECT * FROM work_items WHERE id = ?", (wi_id,)).fetchone()
        return {"work_item": _row(upd)}
    finally:
        conn.close()


@app.delete("/api/work-items/{wi_id}")
def delete_work_item_endpoint(
    wi_id: str, _: None = Depends(require_api_key)
) -> dict[str, Any]:
    conn = _open_rw()
    logger = FactoryLogger(conn)
    try:
        n, err = delete_work_item_subtree(conn, logger, wi_id)
        if err:
            if err == "work_item not found":
                raise HTTPException(status_code=404, detail=err)
            raise HTTPException(status_code=400, detail=err)
        conn.commit()
        return {"ok": True, "deleted_count": n}
    finally:
        conn.close()


@app.post("/api/bulk/archive")
def post_bulk_archive(
    body: dict[str, Any] = Body(default={}),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Архивирует несколько корней (обычно Vision в done)."""
    ids: list[str] | None = body.get("ids")
    filt = (body.get("filter") or "").strip()
    factory = wire(_db_path())
    conn = factory["conn"]
    sm = factory["sm"]
    try:
        if filt == "all_done_visions":
            target_ids = list_done_vision_roots_ready_to_archive(conn)
        elif isinstance(ids, list) and ids:
            target_ids = [str(x) for x in ids]
        else:
            raise HTTPException(
                status_code=400,
                detail='body must contain ids: [...] or filter: "all_done_visions"',
            )
        total = 0
        errors: list[str] = []
        for vid in target_ids:
            n, err = archive_work_item_subtree(
                sm, conn, vid, actor_role=Role.CREATOR.value
            )
            if err:
                errors.append(f"{vid}: {err}")
            else:
                total += n
        return {
            "ok": not errors,
            "archived_count": total,
            "errors": errors,
            "processed_roots": len(target_ids),
        }
    finally:
        conn.close()


@app.post("/api/work-items/{wi_id}/run")
def post_work_item_run(wi_id: str, _: None = Depends(require_api_key)) -> dict[str, Any]:
    """Запуск forge для атома (тот же путь, что POST /api/tasks/…/forge-run в dashboard_api)."""
    from .dashboard_task_run import accept_dashboard_task_run

    ok, body, status = accept_dashboard_task_run(wi_id)
    if not ok:
        raise HTTPException(status_code=status, detail=body)
    return {
        "started": True,
        "run_id": body.get("run_id"),
        "ok": body.get("ok", True),
        "status": body.get("status", "started"),
        "message": body.get("message", "accepted"),
    }


@app.post("/api/tasks/{wi_id}/forge-run")
def post_tasks_forge_run_compat(wi_id: str, _: None = Depends(require_api_key)) -> dict[str, Any]:
    """Совместимость с factory-os.html (старый путь)."""
    return post_work_item_run(wi_id)


@app.get("/api/work-items/{wi_id}")
def get_work_item(wi_id: str) -> dict[str, Any]:
    conn = _open_ro()
    try:
        wi = conn.execute("SELECT * FROM work_items WHERE id = ?", (wi_id,)).fetchone()
        if not wi:
            raise HTTPException(status_code=404, detail="work_item not found")
        files = conn.execute(
            """
            SELECT path, intent, description, required
            FROM work_item_files
            WHERE work_item_id = ?
            ORDER BY path
            """,
            (wi_id,),
        ).fetchall()
        # Atom diagnostics counters (no schema columns; derived from existing tables).
        forge_attempts = conn.execute(
            "SELECT COUNT(*) AS c FROM runs WHERE work_item_id = ? AND role = 'forge'",
            (wi_id,),
        ).fetchone()["c"]
        review_rejections = conn.execute(
            "SELECT COUNT(*) AS c FROM review_results WHERE work_item_id = ? AND verdict != 'approved'",
            (wi_id,),
        ).fetchone()["c"]
        judge_rejections = conn.execute(
            "SELECT COUNT(*) AS c FROM judge_verdicts WHERE work_item_id = ? AND verdict != 'approved'",
            (wi_id,),
        ).fetchone()["c"]
        ch = conn.execute(
            "SELECT * FROM work_items WHERE parent_id = ? ORDER BY created_at",
            (wi_id,),
        ).fetchall()
        qlease = conn.execute(
            """
            SELECT queue_name, lease_owner, lease_until, attempts
            FROM work_item_queue WHERE work_item_id = ?
            """,
            (wi_id,),
        ).fetchone()
        wi_out = {
            **_row(wi),
            "files": _rows(files),
            "forge_attempts": int(forge_attempts),
            "review_rejections": int(review_rejections),
            "judge_rejections": int(judge_rejections),
        }
        if qlease:
            wi_out["queue_lease"] = _row(qlease)
        return {
            "work_item": wi_out,
            "children": _rows(ch),
        }
    finally:
        conn.close()


@app.get("/api/tasks/{wi_id}")
def get_task_bundle(wi_id: str) -> dict[str, Any]:
    """Совместимость с factory-os.html (openDetail)."""
    conn = _open_ro()
    try:
        wi = conn.execute("SELECT * FROM work_items WHERE id = ?", (wi_id,)).fetchone()
        if not wi:
            raise HTTPException(status_code=404, detail="work_item not found")
        runs = conn.execute(
            """
            SELECT id, role, run_type, status, started_at, finished_at, work_item_id,
                   error_summary, tokens_used, source_run_id, dry_run
            FROM runs WHERE work_item_id = ?
            ORDER BY started_at DESC
            """,
            (wi_id,),
        ).fetchall()
        files = conn.execute(
            """
            SELECT path, intent, description, required
            FROM work_item_files WHERE work_item_id = ?
            ORDER BY path
            """,
            (wi_id,),
        ).fetchall()
        ev = conn.execute(
            """
            SELECT id, event_time, event_type, actor_role, severity, message
            FROM event_log
            WHERE work_item_id = ?
            ORDER BY event_time DESC, id DESC
            LIMIT 10
            """,
            (wi_id,),
        ).fetchall()
        return {
            "work_item": {**_row(wi), "files": _rows(files), "event_log": _rows(ev)},
            "runs": _serialize_runs(runs),
            "comments": [],
        }
    finally:
        conn.close()


@app.get("/api/work_items")
def work_items_legacy(id: str | None = None) -> dict[str, Any]:
    if not id:
        return {"work_item": None, "error": "id required"}
    conn = _open_ro()
    try:
        wi = conn.execute("SELECT * FROM work_items WHERE id = ?", (id,)).fetchone()
        if not wi:
            return {"work_item": None, "error": "not found"}
        return {"work_item": _row(wi)}
    finally:
        conn.close()


@app.get("/api/work-items/{wi_id}/runs")
def runs_for_work_item(wi_id: str) -> dict[str, Any]:
    conn = _open_ro()
    try:
        rows = conn.execute(
            """
            SELECT id, role, run_type, status, started_at, finished_at
                   , source_run_id, dry_run
            FROM runs WHERE work_item_id = ?
            ORDER BY started_at DESC
            """,
            (wi_id,),
        ).fetchall()
        return {"items": _serialize_runs(rows)}
    finally:
        conn.close()


@app.get("/api/runs")
def list_runs(
    work_item_id: str | None = None,
    limit: int = Query(120, ge=1, le=500),
) -> dict[str, Any]:
    conn = _open_ro()
    try:
        if work_item_id:
            rows = conn.execute(
                """
                SELECT id, role, run_type, status, started_at, finished_at, work_item_id
                       , source_run_id, dry_run
                FROM runs WHERE work_item_id = ?
                ORDER BY started_at DESC LIMIT ?
                """,
                (work_item_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, role, run_type, status, started_at, finished_at, work_item_id
                       , source_run_id, dry_run
                FROM runs ORDER BY started_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return {"items": _serialize_runs(rows)}
    finally:
        conn.close()


@app.get("/api/runs/{run_id}")
def get_run_detail(run_id: str) -> dict[str, Any]:
    conn = _open_ro()
    try:
        r = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if r:
            effective_run_id = resolve_effective_run_id(conn, run_id) or run_id
            steps = conn.execute(
                "SELECT * FROM run_steps WHERE run_id = ? ORDER BY step_no",
                (effective_run_id,),
            ).fetchall()
            fcs = conn.execute(
                "SELECT * FROM file_changes WHERE run_id = ? ORDER BY created_at",
                (effective_run_id,),
            ).fetchall()
            return {
                "run": {**_serialize_run_row(r), "effective_run_id": effective_run_id},
                "run_steps": _rows(steps),
                "file_changes": _rows(fcs),
            }
        rows = conn.execute(
            """
            SELECT * FROM runs WHERE work_item_id = ?
            ORDER BY started_at DESC
            """,
            (run_id,),
        ).fetchall()
        if rows:
            return {"runs": _serialize_runs(rows), "work_item_id": run_id}
        raise HTTPException(status_code=404, detail="run / work_item not found")
    finally:
        conn.close()


@app.get("/api/runs/{run_id}/steps")
def get_run_steps(run_id: str) -> dict[str, Any]:
    conn = _open_ro()
    try:
        effective_run_id = resolve_effective_run_id(conn, run_id) or run_id
        steps = conn.execute(
            "SELECT * FROM run_steps WHERE run_id = ? ORDER BY step_no",
            (effective_run_id,),
        ).fetchall()
        if not steps:
            raise HTTPException(status_code=404, detail="no steps for this run id")
        return {"items": _rows(steps), "effective_run_id": effective_run_id}
    finally:
        conn.close()


@app.get("/api/runs/{run_id}/effective")
def get_effective_run_id(run_id: str) -> dict[str, Any]:
    conn = _open_ro()
    try:
        r = conn.execute("SELECT id FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="run not found")
        return {"effective_run_id": resolve_effective_run_id(conn, run_id) or run_id}
    finally:
        conn.close()


@app.get("/api/events")
def list_events(
    limit: int = Query(10, ge=1, le=500),
    work_item_id: str | None = None,
    event_type: str | None = None,
) -> dict[str, Any]:
    conn = _open_ro()
    try:
        q = "SELECT * FROM event_log WHERE 1=1"
        params: list[Any] = []
        if work_item_id:
            q += " AND work_item_id = ?"
            params.append(work_item_id)
        if event_type:
            q += " AND event_type LIKE ?"
            params.append(f"%{event_type}%")
        q += " ORDER BY event_time DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        return {"items": _rows(rows), "limit": limit}
    finally:
        conn.close()


@app.get("/api/journal")
def journal(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    work_item_id: str | None = None,
    run_id: str | None = None,
    root_id: str | None = None,
    kind: str | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    conn = _open_ro()
    try:
        flt = JournalFilters(
            work_item_id=work_item_id,
            run_id=run_id,
            root_id=root_id,
            kind=kind,
            role=role,
        )
        return api_journal_query(conn, flt, limit=limit, offset=offset)
    finally:
        conn.close()


def _load_judgements_items(
    conn: sqlite3.Connection, work_item_id: str | None, limit: int
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    qjv = """
        SELECT id, work_item_id, verdict, payload_json, failed_guards_json,
               rejection_reason_code, created_at, run_id
        FROM judge_verdicts
        WHERE 1=1
    """
    pjv: list[Any] = []
    if work_item_id:
        qjv += " AND work_item_id = ?"
        pjv.append(work_item_id)
    qjv += " ORDER BY created_at DESC LIMIT ?"
    pjv.append(limit)
    try:
        jv = conn.execute(qjv, pjv).fetchall()
    except sqlite3.OperationalError as e:
        _LOG.debug("judge_verdicts table unavailable while loading judgements: %s", e)
        jv = []
    for r in jv:
        issues: Any = []
        p: dict[str, Any] = {}
        try:
            p = json.loads(r["payload_json"] or "{}")
            if isinstance(p, dict):
                issues = p.get("failed_guards") or p.get("issues") or []
            else:
                issues = []
        except json.JSONDecodeError:
            issues = []
        try:
            if r["failed_guards_json"]:
                issues = json.loads(r["failed_guards_json"])
        except (json.JSONDecodeError, TypeError) as e:
            _LOG.debug("Failed to parse failed_guards_json for verdict %s: %s", r["id"], e)
        used_el = None
        if isinstance(p, dict):
            used_el = p.get("used_event_log")
        items.append(
            {
                "id": r["id"],
                "work_item_id": r["work_item_id"],
                "role": "judge",
                "verdict": r["verdict"],
                "reason_code": r["rejection_reason_code"] or "",
                "issues": issues if isinstance(issues, list) else [],
                "created_at": r["created_at"],
                "run_id": r["run_id"],
                "summary": (r["verdict"] or "")[:200],
                "used_event_log": used_el if isinstance(used_el, bool) else False,
            }
        )
    qrr = """
        SELECT id, work_item_id, verdict, issues_json, payload_json, created_at, reviewer_run_id
        FROM review_results
        WHERE 1=1
    """
    prr: list[Any] = []
    if work_item_id:
        qrr += " AND work_item_id = ?"
        prr.append(work_item_id)
    qrr += " ORDER BY created_at DESC LIMIT ?"
    prr.append(limit)
    try:
        rr = conn.execute(qrr, prr).fetchall()
    except sqlite3.OperationalError as e:
        _LOG.debug("review_results table unavailable while loading judgements: %s", e)
        rr = []
    for r in rr:
        issues = []
        try:
            issues = json.loads(r["issues_json"] or "[]")
        except json.JSONDecodeError:
            issues = []
        items.append(
            {
                "id": r["id"],
                "work_item_id": r["work_item_id"],
                "role": "reviewer",
                "verdict": r["verdict"],
                "reason_code": "",
                "issues": issues,
                "created_at": r["created_at"],
                "run_id": r["reviewer_run_id"],
                "summary": (r["verdict"] or "")[:200],
            }
        )
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return items[:limit]


@app.get("/api/judgements")
def judgements(
    work_item_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    conn = _open_ro()
    try:
        return {"items": _load_judgements_items(conn, work_item_id, limit)}
    finally:
        conn.close()


@app.get("/api/verdicts")
@app.get("/api/judge_verdicts")
def judge_verdicts(
    work_item_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Compatibility endpoint: always returns a JSON list for dashboard verdict pages."""
    conn = _open_ro()
    try:
        return _load_judgements_items(conn, work_item_id, limit)
    finally:
        conn.close()


@app.get("/api/tree")
def tree() -> dict[str, Any]:
    conn = _open_ro()
    try:
        roots = build_work_items_tree(conn)
        return {"roots": roots}
    finally:
        conn.close()


@app.get("/api/analytics")
def api_analytics(
    period: str = Query("24h", description="24h | 7d | 30d | all"),
) -> dict[str, Any]:
    """Метрики фабрики за период (read-only)."""
    p = (period or "24h").strip().lower()
    if p not in ("24h", "7d", "30d", "all"):
        raise HTTPException(
            status_code=400,
            detail="period must be one of: 24h, 7d, 30d, all",
        )
    conn = _open_ro()
    try:
        return compute_analytics(conn, p)
    finally:
        conn.close()


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    conn = _open_ro()
    try:
        by_kind = {r["kind"]: r["c"] for r in conn.execute("SELECT kind, COUNT(*) AS c FROM work_items GROUP BY kind")}
        by_status = {
            r["status"]: r["c"] for r in conn.execute("SELECT status, COUNT(*) AS c FROM work_items GROUP BY status")
        }
        runs_total = conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"]
        last_ev = conn.execute("SELECT MAX(event_time) AS t FROM event_log").fetchone()["t"]
        wi_total = conn.execute("SELECT COUNT(*) AS c FROM work_items").fetchone()["c"]
        total_visions = conn.execute(
            "SELECT COUNT(*) AS c FROM work_items WHERE kind = 'vision'"
        ).fetchone()["c"]
        total_atoms = conn.execute(
            "SELECT COUNT(*) AS c FROM work_items WHERE kind = 'atom'"
        ).fetchone()["c"]
        total_forge_runs = conn.execute(
            "SELECT COUNT(*) AS c FROM runs WHERE role = 'forge'"
        ).fetchone()["c"]
        last_forge = conn.execute(
            "SELECT MAX(finished_at) AS t FROM runs WHERE role = 'forge' AND finished_at IS NOT NULL"
        ).fetchone()["t"]
        improvements_proposed = 0
        improvements_stats: dict[str, int] = {}
        try:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS c FROM improvement_candidates GROUP BY status
                """
            ).fetchall()
            improvements_stats = {r["status"]: int(r["c"]) for r in rows}
            improvements_proposed = int(improvements_stats.get("proposed", 0))
        except sqlite3.OperationalError as e:
            _LOG.debug("improvement_candidates table unavailable in stats: %s", e)
        orch_hb = _orchestrator_heartbeat_from_conn(conn)
        try:
            wst = workers_status_payload(conn)
        except sqlite3.OperationalError as e:
            _LOG.debug("workers_status_payload fallback due to sqlite operational error: %s", e)
            wst = {"active": 0, "workers": [], "leases_total": 0}
        return {
            "active_workers": int(wst.get("active") or 0),
            "worker_leases_total": int(wst.get("leases_total") or 0),
            "workers_snapshot": wst.get("workers") or [],
            "work_items_total": wi_total,
            "by_kind": by_kind,
            "by_status": by_status,
            "runs_total": runs_total,
            "last_event_time": last_ev,
            "total_visions": int(total_visions),
            "total_atoms": int(total_atoms),
            "total_forge_runs": int(total_forge_runs),
            "last_forge_run_at": last_forge,
            "improvements_proposed": improvements_proposed,
            "improvements_stats": improvements_stats,
            **orch_hb,
        }
    finally:
        conn.close()


@app.get("/api/workers/status")
def api_workers_status() -> dict[str, Any]:
    """Активные lease в очередях (внешние worker-процессы и оркестратор)."""
    conn = _open_ro()
    try:
        return workers_status_payload(conn)
    finally:
        conn.close()


@app.get("/api/improvements")
def list_improvements() -> dict[str, Any]:
    conn = _open_ro()
    try:
        try:
            rows = conn.execute(
                """
                SELECT id, source_type, source_ref, title, description, evidence,
                       fix_target, affected_role, priority_score, status, risk_level,
                       frequency, vision_id, created_at, reviewed_at, reviewed_by
                FROM improvement_candidates
                ORDER BY priority_score DESC, created_at DESC
                """
            ).fetchall()
        except sqlite3.OperationalError as e:
            _LOG.debug("improvement_candidates table unavailable in list_improvements: %s", e)
            return {"candidates": [], "stats": {}}
        candidates = []
        for r in rows:
            candidates.append(
                {
                    "id": r["id"],
                    "source_type": r["source_type"],
                    "source_ref": r["source_ref"],
                    "title": r["title"],
                    "description": r["description"],
                    "evidence": r["evidence"],
                    "fix_target": r["fix_target"],
                    "affected_role": r["affected_role"],
                    "priority_score": float(r["priority_score"])
                    if r["priority_score"] is not None
                    else None,
                    "status": r["status"],
                    "risk_level": r["risk_level"],
                    "frequency": int(r["frequency"] or 0),
                    "vision_id": r["vision_id"],
                    "created_at": r["created_at"],
                    "reviewed_at": r["reviewed_at"],
                    "reviewed_by": r["reviewed_by"],
                }
            )
        st_rows = conn.execute(
            """
            SELECT status, COUNT(*) AS c FROM improvement_candidates GROUP BY status
            """
        ).fetchall()
        stats = {x["status"]: int(x["c"]) for x in st_rows}
        for k in ("proposed", "approved", "converted", "rejected", "expired"):
            stats.setdefault(k, 0)
        return {"candidates": candidates, "stats": stats}
    finally:
        conn.close()


@app.post("/api/improvements/{ic_id}/approve")
def approve_improvement(
    ic_id: str,
    body: dict[str, Any] = Body(default={}),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    reviewed_by = str(body.get("reviewed_by") or "dashboard").strip() or "dashboard"
    conn = _open_rw()
    try:
        row = conn.execute(
            "SELECT id, status FROM improvement_candidates WHERE id = ?", (ic_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        if row["status"] != "proposed":
            raise HTTPException(status_code=400, detail="only proposed can be approved")
        now = _utc_now_iso()
        conn.execute(
            """
            UPDATE improvement_candidates
            SET status = 'approved', reviewed_at = ?, reviewed_by = ?
            WHERE id = ?
            """,
            (now, reviewed_by, ic_id),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/improvements/{ic_id}/reject")
def reject_improvement(ic_id: str, _: None = Depends(require_api_key)) -> dict[str, Any]:
    conn = _open_rw()
    try:
        row = conn.execute(
            "SELECT id, status FROM improvement_candidates WHERE id = ?", (ic_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        if row["status"] != "proposed":
            raise HTTPException(status_code=400, detail="only proposed can be rejected")
        conn.execute(
            "UPDATE improvement_candidates SET status = 'rejected' WHERE id = ?",
            (ic_id,),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/improvements/{ic_id}/convert")
def convert_improvement(ic_id: str, _: None = Depends(require_api_key)) -> dict[str, Any]:
    from .factory_introspect import FactoryIntrospector

    conn = _open_rw()
    try:
        logger = FactoryLogger(conn)
        intro = FactoryIntrospector()
        try:
            vid = intro.convert_one(conn, ic_id, logger)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        conn.commit()
        return {"ok": True, "vision_id": vid}
    finally:
        conn.close()


@app.get("/api/queue/forge_inbox")
def queue_forge_inbox() -> dict[str, Any]:
    """Совместимость с factory-os.html (тот же контракт, что legacy ``dashboard_api``)."""
    conn = _open_ro()
    try:
        return api_forge_inbox_simple(conn)
    finally:
        conn.close()


@app.get("/api/fsm/work_item")
def fsm_work_item() -> dict[str, Any]:
    conn = _open_ro()
    try:
        return _fsm_stub(conn)
    finally:
        conn.close()


@app.get("/api/agents")
def agents_list_compat() -> dict[str, Any]:
    conn = _open_ro()
    try:
        return _agents(conn)
    finally:
        conn.close()


@app.get("/api/failure-clusters")
def failure_clusters() -> dict[str, Any]:
    return {"clusters": [], "items": []}


@app.get("/api/failures")
def failures() -> dict[str, Any]:
    """Alias for /api/failure-clusters for frontend compatibility."""
    return {"clusters": [], "items": []}


@app.get("/api/hr")
def hr_stub() -> dict[str, Any]:
    return {"policies": [], "proposals": []}


@app.get("/api/visions")
def visions() -> dict[str, Any]:
    conn = _open_ro()
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


@app.post("/api/visions")
def create_vision(
    body: dict[str, Any] = Body(...),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """
    Создаёт Vision и запускает planner (синхронно, MVP).
    Ответ: ``ok``, ``id``, ``title``, ``tree`` (один корень Vision с детьми), ``tree_stats``, ``reasoning``.
    """
    title = str(body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail={"error": "title is required"})
    description = body.get("description")
    description = str(description).strip() if description is not None else None

    conn: sqlite3.Connection | None = None
    try:
        from .db import init_db  # lazy import

        # ensure schema exists + seed accounts/agents on a dedicated connection
        # (если оркестратор уже держит write-транзакцию, DDL может попасть в lock;
        #  в этом случае предполагаем, что схема уже создана при startup).
        try:
            tmp = init_db(_db_path())
            tmp.close()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                raise

        conn = _open_rw()
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
        # stats: из контракта planner output
        def _stats(items) -> dict[str, int]:
            c = {"epics": 0, "stories": 0, "tasks": 0, "atoms": 0}
            def walk(it):
                k = it.kind
                if k == "epic": c["epics"] += 1
                elif k == "story": c["stories"] += 1
                elif k == "task": c["tasks"] += 1
                elif k == "atom": c["atoms"] += 1
                for ch in it.children: walk(ch)
            for it in items: walk(it)
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
                _LOG.debug("Failed to close vision creation DB connection: %s", e, exc_info=True)


# ═══════════════════════════════════════════════════════
# VISION DECOMPOSE (Qwen)
# ═══════════════════════════════════════════════════════

@app.post("/api/visions/{vision_id}/decompose")
def decompose_vision_endpoint(
    vision_id: str,
    body: dict[str, Any] = Body(...),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """
    Авто-декомпозиция Vision через Qwen.
    Возвращает иерархию: epics → stories → tasks → atoms.
    """
    title = str(body.get("title") or "").strip()
    description = str(body.get("description") or "").strip()
    
    if not title:
        raise HTTPException(status_code=400, detail={"error": "title is required"})
    
    # Промпт для Qwen
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
        # Вызов Qwen CLI
        result = run_qwen_cli(prompt=prompt, timeout=300)
        
        # Парсинг JSON ответа
        import re
        json_match = re.search(r'\{.*\}', result, re.DOTALL)
        if json_match:
            hierarchy = json.loads(json_match.group())
        else:
            hierarchy = json.loads(result)
        
        return {"hierarchy": hierarchy, "ok": True}
        
    except json.JSONDecodeError as e:
        _get_logger().error(f'Qwen decompose JSON error: {e}')
        raise HTTPException(status_code=500, detail={"error": "Invalid JSON from Qwen", "message": str(e)})
    except Exception as e:
        _get_logger().error(f'Qwen decompose error: {e}')
        raise HTTPException(status_code=500, detail={"error": "Decompose failed", "message": str(e)})


# ═══════════════════════════════════════════════════════
# CHAT (Qwen SSE)
# ═══════════════════════════════════════════════════════

@app.post("/api/chat/qwen")
async def chat_qwen_create(request: Request) -> dict[str, str]:
    """
    Создать сессию чата с Qwen.
    Возвращает chat_id для подключения к SSE потоку.
    """
    from .db import init_db

    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    prompt = body.get("prompt", "")
    context = body.get("context", {})
    work_item_id = body.get("work_item_id")

    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")

    try:
        tmp = init_db(_db_path())
        tmp.close()
    except sqlite3.OperationalError as e:
        if "locked" not in str(e).lower():
            raise

    conn = _open_rw()
    account_manager = AccountManager(conn, FactoryLogger(conn))

    # ChatService теперь принимает db_path и создаёт свои соединения
    service = ChatService(_db_path(), account_manager)

    full_context = context or {}
    if work_item_id:
        work_item = conn.execute(
            "SELECT * FROM work_items WHERE id = ?",
            (work_item_id,)
        ).fetchone()
        if work_item:
            full_context.update({
                'work_item_id': work_item_id,
                'kind': work_item['kind'],
                'title': work_item['title'],
                'description': work_item['description'],
                'status': work_item['status']
            })

    chat_id = service.create_chat_session(prompt, full_context)
    conn.close()

    return {"chat_id": chat_id}


@app.get("/api/chat/qwen/{chat_id}/stream")
async def chat_qwen_stream(chat_id: str):
    """SSE поток для чата с Qwen."""
    from starlette.responses import StreamingResponse
    from .db import init_db

    try:
        tmp = init_db(_db_path())
        tmp.close()
    except sqlite3.OperationalError as e:
        if "locked" not in str(e).lower():
            raise

    conn = _open_rw()
    account_manager = AccountManager(conn, FactoryLogger(conn))
    service = ChatService(_db_path(), account_manager)

    async def generate():
        async for chunk in service.stream_chat_response(chat_id):
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# ═══════════════════════════════════════════════════════
# QWEN FIX (Auto-fix for Forge errors)
# ═══════════════════════════════════════════════════════

@app.post("/api/qwen/fix")
def qwen_fix_endpoint(
    body: dict[str, Any] = Body(...),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """
    Запрос исправления ошибки у Qwen.
    Используется для авто-исправления Forge ошибок.
    """
    error_type = str(body.get("type") or "unknown").strip()
    message = str(body.get("message") or "").strip()
    context = body.get("context", {})
    
    if not message:
        raise HTTPException(status_code=400, detail={"error": "message is required"})
    
    # Промпт для Qwen
    prompt = f"""
Произошла ошибка при выполнении Forge задачи.

Тип ошибки: {error_type}
Сообщение: {message}
Контекст: {json.dumps(context, indent=2)}

Проанализируй ошибку и предложи исправление.
Верни ТОЛЬКО JSON без markdown:
{{
  "suggestion": "Описание проблемы и решения",
  "files": ["path/to/file.py"],
  "changes": [
    {{
      "file": "path/to/file.py",
      "action": "modify",
      "content": "Новое содержимое файла или diff"
    }}
  ],
  "confidence": 0.95
}}
"""
    
    try:
        # Вызов Qwen CLI
        result = run_qwen_cli(prompt=prompt, timeout=120)
        
        # Парсинг JSON ответа
        import re
        json_match = re.search(r'\{.*\}', result, re.DOTALL)
        if json_match:
            fix = json.loads(json_match.group())
        else:
            fix = json.loads(result)
        
        return {"fix": fix, "ok": True}
        
    except json.JSONDecodeError as e:
        _get_logger().error(f'Qwen fix JSON error: {e}')
        raise HTTPException(status_code=500, detail={"error": "Invalid JSON from Qwen", "message": str(e)})
    except Exception as e:
        _get_logger().error(f'Qwen fix error: {e}')
        raise HTTPException(status_code=500, detail={"error": "Fix failed", "message": str(e)})


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(description="Factory read-only HTTP API (SQLite)")
    p.add_argument("--db", help="Путь к SQLite (иначе FACTORY_DB / factory.db)")
    p.add_argument("--host", default=os.environ.get("FACTORY_API_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("FACTORY_API_PORT", "8000")))
    args = p.parse_args(argv)
    if args.db:
        os.environ["FACTORY_DB"] = args.db
    host, port = args.host, args.port
    print(f"Factory read-only API: http://{host}:{port}  DB={_db_path()}", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
