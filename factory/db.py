"""Подключение к SQLite, транзакции и DB helpers."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
import asyncio
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

try:
    import aiosqlite
except ModuleNotFoundError:  # pragma: no cover - fallback for offline test environments
    class _AsyncCursor:
        def __init__(self, cursor: sqlite3.Cursor) -> None:
            self._cursor = cursor

        async def fetchone(self):
            return await asyncio.to_thread(self._cursor.fetchone)

        async def fetchall(self):
            return await asyncio.to_thread(self._cursor.fetchall)

    class _AsyncConnection:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn
            self.row_factory = sqlite3.Row

        async def execute(self, sql: str, params: tuple[Any, ...] = ()):
            cur = await asyncio.to_thread(self._conn.execute, sql, params)
            return _AsyncCursor(cur)

        async def close(self) -> None:
            await asyncio.to_thread(self._conn.close)

    class _AioSqliteCompat:
        Row = sqlite3.Row
        OperationalError = sqlite3.OperationalError

        @staticmethod
        async def connect(*args: Any, **kwargs: Any) -> _AsyncConnection:
            conn = await asyncio.to_thread(sqlite3.connect, *args, **kwargs)
            conn.row_factory = sqlite3.Row
            return _AsyncConnection(conn)

    aiosqlite = _AioSqliteCompat()  # type: ignore[assignment]

from .config import ACCOUNTS, DB_PATH, SQLITE_BUSY_TIMEOUT_MS, SQLITE_TIMEOUT_SECONDS
from .db_migrations import ensure_schema, migrate_schema
from .models import Role
from .schema_ddl_aux import (
    ARCHITECT_COMMENTS_INDEX_TIME_DDL,
    ARCHITECT_COMMENTS_INDEX_WI_DDL,
    ARCHITECT_COMMENTS_TABLE_DDL,
    IMPROVEMENT_CANDIDATES_DDL,
    JUDGE_AND_REVIEW_RESULTS_DDL,
    MIGRATIONS_TABLE_DDL,
)

_SQLITE_TIMEOUT_SEC = SQLITE_TIMEOUT_SECONDS
_SQLITE_BUSY_TIMEOUT_MS = SQLITE_BUSY_TIMEOUT_MS
_LOG = logging.getLogger("factory.db")


def _db_path_from_conn(conn: sqlite3.Connection) -> Path | None:
    """Путь к файлу БД для `main` (для делегирования ensure_schema из legacy-вызовов)."""
    for _seq, name, path in conn.execute("PRAGMA database_list").fetchall():
        if name == "main" and path:
            return Path(path)
    return None


def _connect_sqlite(
    db_path: Path | str,
    *,
    read_only: bool = False,
) -> sqlite3.Connection:
    """Единая точка открытия SQLite-соединений с согласованными PRAGMA."""
    p = Path(db_path).resolve()
    if read_only:
        if not p.exists():
            raise FileNotFoundError(str(p))
        uri = p.as_uri() + "?mode=ro"
        conn = sqlite3.connect(
            uri,
            uri=True,
            timeout=_SQLITE_TIMEOUT_SEC,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA query_only = ON")
        return conn

    conn = sqlite3.connect(
        str(p),
        timeout=_SQLITE_TIMEOUT_SEC,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA wal_autocheckpoint = 100")
    return conn


def ensure_improvement_candidates_schema(conn: sqlite3.Connection) -> None:
    """
    Раньше выполнял DDL на каждом init_db. Сейчас схема поднимается только в ensure_schema.

    Оставлено для совместимости: при необходимости вызывает ensure_schema по пути соединения.
    """
    p = _db_path_from_conn(conn)
    if p is not None:
        ensure_schema(p)


def stable_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(value: Any) -> str:
    return hashlib.sha256(stable_json_dumps(value).encode("utf-8")).hexdigest()


def resolve_effective_run_id(conn: sqlite3.Connection, run_id: str | None) -> str | None:
    """Возвращает source_run_id (для cache-hit run) либо исходный run_id."""
    if not run_id:
        return None
    row = conn.execute(
        "SELECT COALESCE(source_run_id, id) AS effective_run_id FROM runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if not row:
        return run_id
    return row["effective_run_id"] or run_id


def gen_id(prefix: str = "") -> str:
    short = uuid.uuid4().hex[:12]
    return f"{prefix}_{short}" if prefix else short


def get_connection(db_path: Path | str = DB_PATH, *, read_only: bool = False) -> sqlite3.Connection:
    """Лёгкое подключение: только PRAGMA, без DDL (схема — через ensure_schema).

    ``read_only=True`` — URI ``?mode=ro`` (для API read-path); ``foreign_keys`` включены для единообразия.
    """
    conn = _connect_sqlite(db_path, read_only=read_only)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except sqlite3.OperationalError as e:
        _LOG.debug("Skipping wal_checkpoint(PASSIVE): %s", e)
    return conn


async def get_async_connection(
    db_path: Path | str = DB_PATH,
    *,
    read_only: bool = False,
) -> aiosqlite.Connection:
    """Async SQLite connection for use inside async API handlers."""
    p = Path(db_path).resolve()
    if read_only:
        if not p.exists():
            raise FileNotFoundError(str(p))
        uri = p.as_uri() + "?mode=ro"
        conn = await aiosqlite.connect(
            uri,
            uri=True,
            timeout=_SQLITE_TIMEOUT_SEC,
        )
        await conn.execute(f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS}")
        await conn.execute("PRAGMA query_only = ON")
    else:
        conn = await aiosqlite.connect(
            str(p),
            timeout=_SQLITE_TIMEOUT_SEC,
        )
        await conn.execute("PRAGMA journal_mode = WAL")
        await conn.execute(f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS}")
        await conn.execute("PRAGMA wal_autocheckpoint = 100")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    try:
        await conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except aiosqlite.OperationalError as e:
        _LOG.debug("Skipping wal_checkpoint(PASSIVE): %s", e)
    return conn


def _db_path() -> str:
    """Compatibility helper for legacy imports in API server module."""
    return str(DB_PATH)


def _open_ro() -> sqlite3.Connection:
    """Compatibility helper returning read-only DB connection."""
    return get_connection(DB_PATH, read_only=True)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Generator[sqlite3.Connection, None, None]:
    try:
        yield conn
        conn.commit()
    except Exception as e:
        _LOG.debug("Transaction rollback due to exception: %s", e, exc_info=True)
        conn.rollback()
        raise


def _seed_static_data(conn: sqlite3.Connection) -> None:
    for i, acc in enumerate(ACCOUNTS):
        conn.execute(
            """
            INSERT OR IGNORE INTO api_accounts (id, name, api_key, daily_limit, priority, provider)
            VALUES (?, ?, ?, ?, ?, 'qwen_code_cli')
            """,
            (acc["id"], acc["name"], acc["api_key"], acc["daily_limit"], i),
        )

    default_agents = [
        (Role.ORCHESTRATOR, "orchestrator"),
        (Role.PLANNER, "planner"),
        (Role.ARCHITECT, "architect"),
        (Role.JUDGE, "judge"),
        (Role.REVIEWER, "reviewer"),
        (Role.FORGE, "forge"),
        (Role.HR, "hr"),
    ]
    for role, name in default_agents:
        agent_id = f"agent_{name}"
        conn.execute(
            """
            INSERT OR IGNORE INTO agents (id, role, active)
            VALUES (?, ?, 1)
            """,
            (agent_id, role.value),
        )

    initial_state = {
        "factory_status": "idle",
        "active_account_id": ACCOUNTS[0]["id"],
        "orchestrator_version": "0.1.0",
        "last_poll_at": "",
    }
    for k, v in initial_state.items():
        conn.execute(
            "INSERT OR IGNORE INTO system_state (key, value) VALUES (?, ?)",
            (k, v),
        )

    conn.commit()


def init_db(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    ensure_schema(Path(db_path))
    conn = get_connection(db_path)
    _seed_static_data(conn)
    return conn


def _row(d: sqlite3.Row):
    return {k: d[k] for k in d.keys()}


def _rows(rs):
    return [_row(r) for r in rs]
