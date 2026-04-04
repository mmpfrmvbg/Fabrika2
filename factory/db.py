"""Подключение к SQLite, транзакции, первичная инициализация."""
from __future__ import annotations

import os
import sqlite3
import time
import uuid
import hashlib
import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from .config import ACCOUNTS, DB_PATH
from .models import Role
from .schema_ddl import DDL, V_API_USAGE_TODAY_RECREATE

# Последняя миграция: 1=базовый DDL, 2=improvement_candidates, 3=file_changes.intent_override,
# 4=fsm creator_cancelled/archive_sweep, 5=judge_rejected release locks, 6=cleanup stale locks,
# 7=forensic_tracing_fields, 8=runs_retry_count, 9=runs_source_run_id_dry_run, 10=work_items heartbeat
_SCHEMA_VERSION = 10
_SQLITE_TIMEOUT_SEC = 30.0
_SQLITE_BUSY_TIMEOUT_MS = 30000
_LOG = logging.getLogger("factory.db")

# Migration v2: self-improvement candidates (idempotent CREATE TABLE IF NOT EXISTS)
IMPROVEMENT_CANDIDATES_DDL = """
CREATE TABLE IF NOT EXISTS improvement_candidates (
    id            TEXT PRIMARY KEY,
    source_type   TEXT NOT NULL CHECK (source_type IN (
        'failure_cluster','review_pattern','judge_pattern',
        'metric_anomaly','hr_proposal','retry_hotspot',
        'manual'
    )),
    source_ref    TEXT,
    title         TEXT NOT NULL,
    description   TEXT NOT NULL,
    evidence      TEXT NOT NULL,
    fix_target    TEXT NOT NULL CHECK (fix_target IN (
        'code','prompt','policy','infra','process'
    )),
    affected_role TEXT,
    affected_files TEXT,
    frequency     INTEGER NOT NULL DEFAULT 1,
    severity_score REAL NOT NULL DEFAULT 0.5,
    impact_score  REAL NOT NULL DEFAULT 0.5,
    confidence    REAL NOT NULL DEFAULT 0.5,
    priority_score REAL GENERATED ALWAYS AS (
        severity_score * 0.4 + impact_score * 0.3 + confidence * 0.2
        + MIN(frequency / 10.0, 1.0) * 0.1
    ) STORED,
    status        TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN (
        'proposed','approved','rejected','converted','expired'
    )),
    risk_level    TEXT NOT NULL DEFAULT 'low' CHECK (risk_level IN (
        'low','medium','high'
    )),
    vision_id     TEXT REFERENCES work_items(id),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now')),
    reviewed_at   TEXT,
    reviewed_by   TEXT,
    expires_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ic_status ON improvement_candidates(status, priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_ic_source ON improvement_candidates(source_type);
"""


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
    conn.execute("PRAGMA busy_timeout = 30000")
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


def _file_changes_columns(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(file_changes)").fetchall()}


def _migration_file_changes_intent_override(conn: sqlite3.Connection) -> None:
    cols = _file_changes_columns(conn)
    if "intent_override" not in cols:
        conn.execute("ALTER TABLE file_changes ADD COLUMN intent_override TEXT")


def _migration_fsm_creator_archive_api(conn: sqlite3.Connection) -> None:
    """FSM: creator_cancelled и archive_sweep для API управления (INSERT OR IGNORE)."""
    conn.executescript(
        """
        INSERT OR IGNORE INTO state_transitions VALUES
            ('st_creator_cancel','work_item','*','creator_cancelled','cancelled',
             'guard_cancellable_for_creator','action_creator_cancelled_cleanup',NULL,
             'Создатель отменил задачу (creator_cancelled)'),
            ('st_archive_sweep','work_item','done','archive_sweep','archived',
             'guard_work_item_done','action_archive_finalize',NULL,
             'Архив завершённой задачи (archive_sweep)');
        """
    )


def _migration_fsm_judge_rejected_release_locks(conn: sqlite3.Connection) -> None:
    """
    Миграция 5: Добавляет action_release_file_locks к переходу st_07 (judge_rejected).

    Проблема: атомы в judge_rejected не освобождали блокировки файлов,
    что блокировало все новые атомы с тем же путём.
    """
    # Обновляем st_07 для не-атомов (vision, initiative, epic, story, task)
    conn.execute(
        """
        INSERT OR REPLACE INTO state_transitions VALUES
            ('st_07','work_item','ready_for_judge','judge_rejected','judge_rejected',
             '','action_return_to_author;action_release_file_locks',NULL,
             'Судья отклонил — возврат автору с комментарием + освобождение блокировок')
        """
    )
    # Добавляем явный переход для атомов: judge_rejected -> ready_for_work с освобождением блокировок
    conn.execute(
        """
        INSERT OR IGNORE INTO state_transitions VALUES
            ('st_07d','work_item','ready_for_judge','judge_rejected','ready_for_work',
             '','action_enqueue_forge;action_release_file_locks','["atom"]',
             'Атом отклонён судьёй — в кузницу с освобождением блокировок')
        """
    )


def _migration_cleanup_stale_locks(conn: sqlite3.Connection) -> None:
    """
    Миграция 6: Очищает зависшие блокировки и обновляет статусы застрявших атомов.

    Проблема: при сбое worker'а блокировки файлов не освобождались,
    что блокировало все новые атомы с теми же путями.
    """
    # Освобождаем все истёкшие блокировки (expires_at < now)
    # Используем replace для корректного сравнения ISO дат с 'T'
    conn.execute(
        """
        UPDATE file_locks
        SET released_at = COALESCE(released_at, strftime('%Y-%m-%dT%H:%M:%f','now'))
        WHERE released_at IS NULL
          AND replace(expires_at, 'T', ' ') < datetime('now')
        """
    )
    # Находим work_item_id с зависшими блокировками и статусом judge_rejected/cancelled
    # и переводим их в ready_for_work для повторной обработки
    conn.execute(
        """
        UPDATE work_items
        SET status = 'ready_for_work',
            previous_status = status,
            updated_at = strftime('%Y-%m-%dT%H:%M:%f','now')
        WHERE id IN (
            SELECT DISTINCT fl.work_item_id
            FROM file_locks fl
            WHERE fl.released_at IS NULL
              AND replace(fl.expires_at, 'T', ' ') < datetime('now')
        )
        AND status IN ('judge_rejected', 'in_progress', 'in_review')
        AND kind = 'atom'
        """
    )
    # Обновляем work_item_queue — снимаем lease с зависших задач
    conn.execute(
        """
        UPDATE work_item_queue
        SET lease_owner = NULL, lease_until = NULL
        WHERE work_item_id IN (
            SELECT fl.work_item_id
            FROM file_locks fl
            WHERE fl.released_at IS NULL
        )
        AND lease_owner IS NOT NULL
        """
    )


def _max_migration_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT MAX(version) FROM migrations").fetchone()
        return int(row[0] or 0)
    except sqlite3.OperationalError:
        return 0


def _api_accounts_column_names(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(api_accounts)").fetchall()}


def stable_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(value: Any) -> str:
    return hashlib.sha256(stable_json_dumps(value).encode("utf-8")).hexdigest()


def _migration_forensic_tracing(conn: sqlite3.Connection) -> None:
    alters = [
        "ALTER TABLE runs ADD COLUMN agent_version TEXT",
        "ALTER TABLE runs ADD COLUMN prompt_version TEXT",
        "ALTER TABLE runs ADD COLUMN model_name_snapshot TEXT",
        "ALTER TABLE runs ADD COLUMN model_params_json TEXT",
        "ALTER TABLE runs ADD COLUMN input_hash TEXT",
        "ALTER TABLE run_steps ADD COLUMN agent_version TEXT",
        "ALTER TABLE run_steps ADD COLUMN input_hash TEXT",
        "ALTER TABLE event_log ADD COLUMN caused_by_type TEXT",
        "ALTER TABLE event_log ADD COLUMN caused_by_id TEXT",
    ]
    for stmt in alters:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            _LOG.debug("Skipping forensic migration statement %r: %s", stmt, e)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_input_hash ON runs(input_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rs_input_hash ON run_steps(input_hash)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_el_caused_by ON event_log(caused_by_type, caused_by_id)"
    )


def _migration_runs_retry_count(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ALTER TABLE runs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError as e:
        _LOG.debug("Skipping runs.retry_count migration: %s", e)


def _migration_runs_source_and_dry_run(conn: sqlite3.Connection) -> None:
    alters = [
        "ALTER TABLE runs ADD COLUMN source_run_id TEXT REFERENCES runs(id)",
        "ALTER TABLE runs ADD COLUMN dry_run INTEGER NOT NULL DEFAULT 0",
    ]
    for stmt in alters:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            _LOG.debug("Skipping runs source/dry_run migration statement %r: %s", stmt, e)


def _migration_work_items_last_heartbeat(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ALTER TABLE work_items ADD COLUMN last_heartbeat_at TIMESTAMP")
    except sqlite3.OperationalError as e:
        _LOG.debug("Skipping work_items.last_heartbeat_at migration: %s", e)


@contextmanager
def _advisory_file_lock(
    path: Path, *, timeout_sec: float = 60.0, poll_sec: float = 0.05
) -> Generator[None, None, None]:
    """
    Advisory межпроцессный lock через отдельный файл.
    Нужен, чтобы не выполнять DDL/migrations конкурентно (SQLite executescript берёт exclusive lock).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+b")  # noqa: SIM115
    try:
        if os.name == "nt":
            import msvcrt

            start = time.time()
            while True:
                try:
                    fh.seek(0)
                    fh.write(b"\0")
                    fh.flush()
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.time() - start >= timeout_sec:
                        raise TimeoutError(f"Timeout acquiring migrate lock: {path}")
                    time.sleep(poll_sec)
        else:
            import fcntl

            start = time.time()
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.time() - start >= timeout_sec:
                        raise TimeoutError(f"Timeout acquiring migrate lock: {path}")
                    time.sleep(poll_sec)

        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                except PermissionError as e:
                    # On some Windows setups, unlocking may raise if the region is already unlocked.
                    _LOG.debug("Ignoring Windows file unlock permission error: %s", e)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            try:
                fh.close()
            except Exception as e:
                _LOG.debug("Failed to close advisory lock file handle: %s", e)


def ensure_schema(db_path: Path = DB_PATH) -> None:
    """
    Гарантирует наличие схемы (idempotent).

    DDL/migrations выполняются только под advisory lock
    `{db_path}.migrate.lock`, чтобы параллельные процессы не ловили `database is locked`.
    """
    db_path = Path(db_path)

    # Быстрый чек без lock: если все миграции применены — выходим (без DDL).
    try:
        conn0 = _connect_sqlite(db_path)
        try:
            if _max_migration_version(conn0) >= _SCHEMA_VERSION:
                return
        finally:
            conn0.close()
    except sqlite3.OperationalError as e:
        _LOG.debug("Skipping initial schema check due to sqlite operational error: %s", e)

    lock_path = Path(str(db_path) + ".migrate.lock")
    with _advisory_file_lock(lock_path):
        conn = _connect_sqlite(db_path)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS migrations (
                    version     INTEGER PRIMARY KEY,
                    name        TEXT NOT NULL,
                    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
                )
                """
            )
            mv = _max_migration_version(conn)
            if mv < 1:
                conn.executescript(DDL)
                migrate_schema(conn)
                conn.execute(
                    "INSERT OR IGNORE INTO migrations(version, name) VALUES (1, 'factory_schema_v1')"
                )
                mv = _max_migration_version(conn)

            if mv < 2:
                conn.executescript(IMPROVEMENT_CANDIDATES_DDL)
                conn.execute(
                    "INSERT OR IGNORE INTO migrations(version, name) VALUES (2, 'improvement_candidates')"
                )
                mv = _max_migration_version(conn)

            if mv < 3:
                _migration_file_changes_intent_override(conn)
                conn.execute(
                    "INSERT OR IGNORE INTO migrations(version, name) VALUES (3, 'file_changes_intent_override')"
                )
                mv = _max_migration_version(conn)

            if mv < 4:
                _migration_fsm_creator_archive_api(conn)
                conn.execute(
                    "INSERT OR IGNORE INTO migrations(version, name) VALUES (4, 'fsm_creator_cancelled_archive_sweep')"
                )
                mv = _max_migration_version(conn)

            if mv < 5:
                _migration_fsm_judge_rejected_release_locks(conn)
                conn.execute(
                    "INSERT OR IGNORE INTO migrations(version, name) VALUES (5, 'fsm_judge_rejected_release_locks')"
                )
                mv = _max_migration_version(conn)

            if mv < 6:
                _migration_cleanup_stale_locks(conn)
                conn.execute(
                    "INSERT OR IGNORE INTO migrations(version, name) VALUES (6, 'cleanup_stale_locks')"
                )
                mv = _max_migration_version(conn)

            if mv < 7:
                _migration_forensic_tracing(conn)
                conn.execute(
                    "INSERT OR IGNORE INTO migrations(version, name) VALUES (7, 'forensic_tracing_fields')"
                )
                mv = _max_migration_version(conn)

            if mv < 8:
                _migration_runs_retry_count(conn)
                conn.execute(
                    "INSERT OR IGNORE INTO migrations(version, name) VALUES (8, 'runs_retry_count')"
                )
                mv = _max_migration_version(conn)

            if mv < 9:
                _migration_runs_source_and_dry_run(conn)
                conn.execute(
                    "INSERT OR IGNORE INTO migrations(version, name) VALUES (9, 'runs_source_run_id_dry_run')"
                )
                mv = _max_migration_version(conn)

            if mv < 10:
                _migration_work_items_last_heartbeat(conn)
                conn.execute(
                    "INSERT OR IGNORE INTO migrations(version, name) VALUES (10, 'work_items_last_heartbeat_at')"
                )

            conn.commit()
        finally:
            conn.close()


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


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Добавляет колонки к существующей api_accounts и пересоздаёт v_api_usage_today."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS architect_comments (
            id              TEXT PRIMARY KEY,
            work_item_id    TEXT NOT NULL REFERENCES work_items(id),
            comment         TEXT NOT NULL,
            created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_architect_comments_wi "
        "ON architect_comments(work_item_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_architect_comments_time "
        "ON architect_comments(work_item_id, created_at DESC)"
    )
    cols = _api_accounts_column_names(conn)
    alters: list[tuple[str, str]] = [
        ("account_status", "TEXT NOT NULL DEFAULT 'active'"),
        ("last_error", "TEXT"),
        ("cooldown_until", "TEXT"),
        ("last_used_at", "TEXT"),
    ]
    for name, typedef in alters:
        if name not in cols:
            conn.execute(f"ALTER TABLE api_accounts ADD COLUMN {name} {typedef}")
    conn.execute(
        "UPDATE api_accounts SET provider = 'qwen_code_cli' "
        "WHERE provider IN ('anthropic', '') OR provider IS NULL"
    )
    conn.executescript(V_API_USAGE_TODAY_RECREATE)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS judge_verdicts (
            id                      TEXT PRIMARY KEY,
            run_id                  TEXT NOT NULL REFERENCES runs(id),
            work_item_id            TEXT NOT NULL REFERENCES work_items(id),
            item                    TEXT NOT NULL,
            verdict                 TEXT NOT NULL,
            all_passed              INTEGER NOT NULL,
            next_event              TEXT NOT NULL,
            rejection_reason_code   TEXT,
            checked_guards_json     TEXT NOT NULL DEFAULT '[]',
            failed_guards_json      TEXT,
            context_refs_json       TEXT NOT NULL DEFAULT '[]',
            suggested_action        TEXT,
            payload_json            TEXT NOT NULL,
            created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
        );
        CREATE INDEX IF NOT EXISTS idx_jv_wi ON judge_verdicts(work_item_id);
        CREATE INDEX IF NOT EXISTS idx_jv_verdict ON judge_verdicts(verdict);
        CREATE INDEX IF NOT EXISTS idx_jv_reason ON judge_verdicts(rejection_reason_code);
        CREATE INDEX IF NOT EXISTS idx_jv_created ON judge_verdicts(created_at);
        CREATE TABLE IF NOT EXISTS review_results (
            id                  TEXT PRIMARY KEY,
            reviewer_run_id     TEXT NOT NULL REFERENCES runs(id),
            work_item_id        TEXT NOT NULL REFERENCES work_items(id),
            subject_run_id      TEXT NOT NULL,
            item                TEXT NOT NULL,
            verdict             TEXT NOT NULL,
            all_passed          INTEGER NOT NULL,
            next_event          TEXT NOT NULL,
            issues_json         TEXT NOT NULL,
            context_refs_json   TEXT NOT NULL,
            payload_json        TEXT NOT NULL,
            created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
        );
        CREATE INDEX IF NOT EXISTS idx_rr_wi ON review_results(work_item_id);
        CREATE INDEX IF NOT EXISTS idx_rr_verdict ON review_results(verdict);
        CREATE INDEX IF NOT EXISTS idx_rr_subject ON review_results(subject_run_id);
        CREATE INDEX IF NOT EXISTS idx_rr_created ON review_results(created_at);
        """
    )

    # FSM transitions may evolve; keep key rules in sync for autonomous pipeline.
    # Use INSERT OR REPLACE so existing DBs get the updated behavior.
    conn.executescript(
        """
        INSERT OR REPLACE INTO state_transitions VALUES
            ('st_05','work_item','ready_for_judge','judge_approved','ready_for_work',
             'guard_ready_for_forge','action_enqueue_forge','["atom","atm_change"]',
             'Судья одобрил атом — в кузницу (нужны файлы и отсутствует успешный forge)'),

            ('st_14','work_item','in_review','review_passed','ready_for_judge',
             'guard_all_checks_passed','action_notify_judge','["atom","atm_change"]',
             'Ревью пройдено — к судье на финальное решение'),

            ('st_18','work_item','ready_for_judge','judge_approved','done',
             'guard_has_review_approval','action_commit_to_git','["atom","atm_change"]',
             'Судья одобрил после ревью — завершение + готовность к коммиту'),

            ('st_07b','work_item','ready_for_judge','judge_rejected','ready_for_work',
             'guard_has_review_approval','action_enqueue_forge','["atom","atm_change"]',
             'Судья отклонил после ревью — вернуть в кузницу с фидбэком (retry)'),

            ('st_07c','work_item','ready_for_judge','judge_rejected','ready_for_work',
             'guard_has_file_changes','action_enqueue_forge','["atom","atm_change"]',
             'Судья отклонил после форжа (есть file_changes) — вернуть в кузницу (retry)'),

            ('st_40','work_item','*','parent_complete','done',
             'guard_all_children_done','action_propagate_completion',NULL,
             'Все дети завершены — автозавершение родителя (рекурсивный rollup)');
        """
    )
    conn.commit()


def gen_id(prefix: str = "") -> str:
    short = uuid.uuid4().hex[:12]
    return f"{prefix}_{short}" if prefix else short


def get_connection(db_path: Path | str = DB_PATH, *, read_only: bool = False) -> sqlite3.Connection:
    """Лёгкое подключение: только PRAGMA, без DDL (схема — через ensure_schema).

    ``read_only=True`` — URI ``?mode=ro`` (для API read-path); ``foreign_keys`` включены для единообразия.
    """
    conn = _connect_sqlite(db_path, read_only=read_only)
    conn.execute("PRAGMA foreign_keys = ON")
    # Явный checkpoint при подключении для разблокировки после сбоев
    try:
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except sqlite3.OperationalError as e:
        _LOG.debug("Skipping wal_checkpoint(PASSIVE): %s", e)
    return conn


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


def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    ensure_schema(db_path)
    conn = get_connection(db_path)
    _seed_static_data(conn)
    return conn
