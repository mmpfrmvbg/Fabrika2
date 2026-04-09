"""DB schema migrations and schema bootstrap helpers."""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from .config import DB_PATH, SQLITE_BUSY_TIMEOUT_MS, SQLITE_TIMEOUT_SECONDS
from .schema_ddl import DDL, V_API_USAGE_TODAY_RECREATE
from .schema_ddl_aux import (
    ARCHITECT_COMMENTS_INDEX_TIME_DDL,
    ARCHITECT_COMMENTS_INDEX_WI_DDL,
    ARCHITECT_COMMENTS_TABLE_DDL,
    IMPROVEMENT_CANDIDATES_DDL,
    JUDGE_AND_REVIEW_RESULTS_DDL,
    MIGRATIONS_TABLE_DDL,
)

# Последняя миграция: 1=базовый DDL, 2=improvement_candidates, 3=file_changes.intent_override,
# 4=fsm creator_cancelled/archive_sweep, 5=judge_rejected release locks, 6=cleanup stale locks,
# 7=forensic_tracing_fields, 8=runs_retry_count, 9=runs_source_run_id_dry_run,
# 10=work_items heartbeat, 11=sqlite_performance_indexes, 12=work_items_priority, 13=dead_status,
# 14=correlation_ids
_SCHEMA_VERSION = 15
_SQLITE_TIMEOUT_SEC = SQLITE_TIMEOUT_SECONDS
_SQLITE_BUSY_TIMEOUT_MS = SQLITE_BUSY_TIMEOUT_MS
_LOG = logging.getLogger("factory.db")


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
    conn.execute(
        """
        INSERT OR REPLACE INTO state_transitions VALUES
            ('st_07','work_item','ready_for_judge','judge_rejected','judge_rejected',
             '','action_return_to_author;action_release_file_locks',NULL,
             'Судья отклонил — возврат автору с комментарием + освобождение блокировок')
        """
    )
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
    conn.execute(
        """
        UPDATE file_locks
        SET released_at = COALESCE(released_at, strftime('%Y-%m-%dT%H:%M:%f','now'))
        WHERE released_at IS NULL
          AND replace(expires_at, 'T', ' ') < datetime('now')
        """
    )
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


def _migration_sqlite_performance_indexes(conn: sqlite3.Connection) -> None:
    """Миграция 11: индексы для частых SQLite-паттернов запросов."""
    conn.execute("CREATE INDEX IF NOT EXISTS idx_work_items_status ON work_items(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_work_items_parent_id ON work_items(parent_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_work_items_created_at ON work_items(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_work_item_id ON runs(work_item_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(started_at)")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_runs_active_per_work_item
        ON runs(work_item_id)
        WHERE status IN ('queued','running')
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_log_entity_id ON event_log(entity_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_log_event_type ON event_log(event_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_log_created_at ON event_log(event_time)")


def _migration_work_items_dead_at(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ALTER TABLE work_items ADD COLUMN dead_at TIMESTAMP")
    except sqlite3.OperationalError as e:
        _LOG.debug("Skipping work_items.dead_at migration: %s", e)


def _migration_work_items_priority(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(work_items)").fetchall()}
    if "priority" not in cols:
        conn.execute("ALTER TABLE work_items ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
    conn.execute("UPDATE work_items SET priority = COALESCE(priority, 0)")


def _migration_work_items_dead_status(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(work_items)").fetchall()}
    if "dead_at" not in cols:
        conn.execute("ALTER TABLE work_items ADD COLUMN dead_at TEXT")
    conn.execute(
        """
        INSERT OR REPLACE INTO state_transitions VALUES
            ('st_13','work_item','in_progress','forge_failed','dead',
             'guard_over_retry_limit','action_mark_dead',NULL,
             'Кузница упала окончательно — терминальный dead')
        """
    )


def _migration_correlation_ids(conn: sqlite3.Connection) -> None:
    alters = [
        "ALTER TABLE work_items ADD COLUMN correlation_id TEXT",
        "ALTER TABLE runs ADD COLUMN correlation_id TEXT",
        "ALTER TABLE event_log ADD COLUMN correlation_id TEXT",
    ]
    for stmt in alters:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            _LOG.debug("Skipping correlation migration statement %r: %s", stmt, e)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wi_correlation ON work_items(correlation_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_corr ON runs(correlation_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_el_corr ON event_log(correlation_id)")
    conn.execute(
        """
        UPDATE runs
        SET correlation_id = COALESCE(
            correlation_id,
            (
                SELECT wi.correlation_id
                FROM work_items wi
                WHERE wi.id = runs.work_item_id
            )
        )
        WHERE correlation_id IS NULL
        """
    )
    conn.execute(
        """
        UPDATE event_log
        SET correlation_id = COALESCE(
            correlation_id,
            (
                SELECT COALESCE(r.correlation_id, wi.correlation_id)
                FROM runs r
                LEFT JOIN work_items wi ON wi.id = event_log.work_item_id
                WHERE r.id = event_log.run_id
            ),
            (
                SELECT wi.correlation_id
                FROM work_items wi
                WHERE wi.id = event_log.work_item_id
            )
        )
        WHERE correlation_id IS NULL
        """
    )


def _migration_work_items_reliability_fields(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(work_items)").fetchall()}
    if "idempotency_key" not in cols:
        conn.execute("ALTER TABLE work_items ADD COLUMN idempotency_key TEXT")
    if "deadline_at" not in cols:
        conn.execute("ALTER TABLE work_items ADD COLUMN deadline_at TEXT")
    if "failure_reason" not in cols:
        conn.execute("ALTER TABLE work_items ADD COLUMN failure_reason TEXT")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_wi_idempotency_key_not_null
        ON work_items(idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )


@contextmanager
def _advisory_file_lock(
    path: Path, *, timeout_sec: float = 60.0, poll_sec: float = 0.05
) -> Generator[None, None, None]:
    """Advisory межпроцессный lock через отдельный файл."""
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
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
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
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
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
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
                except PermissionError as e:
                    _LOG.debug("Ignoring Windows file unlock permission error: %s", e)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
        finally:
            try:
                fh.close()
            except Exception as e:
                _LOG.debug("Failed to close advisory lock file handle: %s", e)


def ensure_schema(db_path: Path = DB_PATH) -> None:
    """Гарантирует наличие схемы (idempotent)."""
    db_path = Path(db_path)

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
            conn.execute(MIGRATIONS_TABLE_DDL)
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
                mv = _max_migration_version(conn)

            if mv < 11:
                _migration_sqlite_performance_indexes(conn)
                conn.execute(
                    "INSERT OR IGNORE INTO migrations(version, name) VALUES (11, 'sqlite_performance_indexes')"
                )
                mv = _max_migration_version(conn)

            if mv < 12:
                _migration_work_items_priority(conn)
                conn.execute(
                    "INSERT OR IGNORE INTO migrations(version, name) VALUES (12, 'work_items_priority')"
                )
                mv = _max_migration_version(conn)

            if mv < 13:
                _migration_work_items_dead_status(conn)
                conn.execute(
                    "INSERT OR IGNORE INTO migrations(version, name) VALUES (13, 'work_items_dead_status')"
                )
                mv = _max_migration_version(conn)

            if mv < 14:
                _migration_correlation_ids(conn)
                conn.execute(
                    "INSERT OR IGNORE INTO migrations(version, name) VALUES (14, 'correlation_ids')"
                )
                mv = _max_migration_version(conn)

            if mv < 15:
                _migration_work_items_reliability_fields(conn)
                conn.execute(
                    "INSERT OR IGNORE INTO migrations(version, name) VALUES (15, 'work_items_reliability_fields')"
                )

            conn.commit()
        finally:
            conn.close()


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Добавляет колонки к существующей api_accounts и пересоздаёт v_api_usage_today."""
    conn.execute(ARCHITECT_COMMENTS_TABLE_DDL)
    conn.execute(ARCHITECT_COMMENTS_INDEX_WI_DDL)
    conn.execute(ARCHITECT_COMMENTS_INDEX_TIME_DDL)
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
    conn.executescript(JUDGE_AND_REVIEW_RESULTS_DDL)

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
