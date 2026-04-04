"""Конфигурация и утилиты среды (Фаза 1)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

DB_PATH = Path("factory.db")

_PROEKT_ROOT = Path(__file__).resolve().parent.parent


def env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    """Read integer from env with fallback and optional lower bound."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def env_float(name: str, default: float, *, minimum: float | None = None) -> float:
    """Read float from env with fallback and optional lower bound."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        value = default
    else:
        try:
            value = float(raw)
        except ValueError:
            value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def resolve_db_path(path: Path | None = None) -> Path:
    """
    Абсолютный путь к SQLite фабрики.

    Относительные пути (в т.ч. ``factory.db`` по умолчанию) якорятся к корню ``proekt/``,
    чтобы оркестратор, CLI и dashboard API использовали один файл независимо от CWD.
    """
    raw = path or Path(
        os.environ.get("FACTORY_DB_PATH")
        or os.environ.get("FACTORY_DB")
        or str(DB_PATH)
    )
    p = Path(raw)
    if not p.is_absolute():
        p = (_PROEKT_ROOT / p).resolve()
    else:
        p = p.resolve()
    return p


def get_db_path() -> Path:
    """То же, что :func:`resolve_db_path` без аргументов — путь к ``factory.db`` по умолчанию."""
    return resolve_db_path()


def load_dotenv(path: Path | None = None) -> None:
    candidates = []
    if path:
        candidates.append(path)
    candidates.append(Path(".env"))
    candidates.append(Path(__file__).resolve().parent.parent / ".env")
    seen: set[Path] = set()
    p = None
    for c in candidates:
        try:
            r = c.resolve()
        except OSError:
            continue
        if r in seen or not c.exists():
            continue
        seen.add(r)
        p = c
        break
    if p is None:
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        # Случайно вставленный JSON в .env — пропускаем
        if line.startswith("{") or line.startswith("}") or line.startswith('"'):
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _env_qwen_dry_run_default_true() -> bool:
    """Локально и в тестах dry-run считается включённым по умолчанию."""
    raw = os.environ.get("FACTORY_QWEN_DRY_RUN")
    if raw is None:
        return True
    s = raw.strip().lower()
    return s not in ("0", "false", "no", "off")


def _allow_empty_accounts() -> bool:
    """Разрешить запуск без реальных API-ключей (dev/CI dry-run)."""
    raw = os.environ.get("FACTORY_ALLOW_EMPTY_ACCOUNTS")
    if raw is None:
        return _env_qwen_dry_run_default_true()
    return raw.strip().lower() in ("1", "true", "yes", "on")


def load_accounts() -> list[dict]:
    accounts = []
    for i in range(1, 10):
        key = os.environ.get(f"FACTORY_API_KEY_{i}")
        if not key:
            continue
        name = os.environ.get(f"FACTORY_API_NAME_{i}", f"Account_{i}")
        limit = int(os.environ.get(f"FACTORY_API_LIMIT_{i}", "3000"))
        safe_id = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_") or f"acc_{i}"
        accounts.append(
            {
                "id": f"acc_{safe_id}",
                "name": name,
                "api_key": key,
                "daily_limit": limit,
            }
        )
    if not accounts:
        if _allow_empty_accounts():
            return [
                {
                    "id": "acc_local_dry",
                    "name": "LocalDryRun",
                    "api_key": "dry-run-placeholder",
                    "daily_limit": 3000,
                }
            ]
        raise RuntimeError(
            "Нет API-аккаунтов. Задайте FACTORY_API_KEY_1, FACTORY_API_NAME_1 и т.д. "
            "в окружении или в файле .env (см. .env.example), либо положите "
            "`.qwen/oauth_creds.json` (и при необходимости oauth_creds_2/3.json) в корне репозитория."
        )
    return accounts


def load_qwen_oauth_tokens_from_repo() -> None:
    """
    Если ``FACTORY_API_KEY_n`` не задан — подставить ``access_token`` из JSON Qwen OAuth.

    Ищет в ``FACTORY_QWEN_OAUTH_DIR`` или в ``<корень репозитория>/.qwen/``:
    ``oauth_creds.json``, ``oauth_creds_2.json``, ``oauth_creds_3.json``.
    """
    # factory/config.py → …/proekt/factory → корень репо на 4 уровня вверх
    repo = Path(__file__).resolve().parent.parent.parent.parent
    qd = Path(os.environ.get("FACTORY_QWEN_OAUTH_DIR") or (repo / ".qwen"))
    mapping = [
        (1, "oauth_creds.json"),
        (2, "oauth_creds_2.json"),
        (3, "oauth_creds_3.json"),
    ]
    default_names = ("Alpha", "Beta", "Gamma")
    for idx, fname in mapping:
        ek = f"FACTORY_API_KEY_{idx}"
        if os.environ.get(ek, "").strip():
            continue
        fp = qd / fname
        if not fp.is_file():
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            tok = (data.get("access_token") or "").strip()
            if not tok:
                continue
            os.environ[ek] = tok
            os.environ.setdefault(f"FACTORY_API_NAME_{idx}", default_names[idx - 1])
            os.environ.setdefault(f"FACTORY_API_LIMIT_{idx}", "3000")
        except (OSError, json.JSONDecodeError, TypeError):
            continue


load_dotenv()
load_qwen_oauth_tokens_from_repo()
ACCOUNTS = load_accounts()

MAX_ATOM_RETRIES = 3
MAX_DECOMPOSITION_DEPTH = 5
ORCHESTRATOR_POLL_INTERVAL = 2.0
MAX_CONCURRENT_FORGE_RUNS = 3
# Сколько одновременных review-run (role=reviewer, running) допускаем при фоновом режиме
MAX_CONCURRENT_REVIEW_RUNS = int(os.environ.get("FACTORY_MAX_CONCURRENT_REVIEW_RUNS", "3"))
# Проактивный Архитектор (Фаза 2): раз в N тиков основного цикла
ORCHESTRATOR_ARCHITECT_SCAN_TICKS = int(
    os.environ.get("FACTORY_ARCHITECT_SCAN_TICKS", "10")
)
API_HOST = os.environ.get("FACTORY_API_HOST", "127.0.0.1")
API_PORT = env_int("FACTORY_API_PORT", 8000, minimum=1)
WORKER_POLL_SECONDS = env_float("FACTORY_WORKER_POLL", 5.0, minimum=0.5)
WORKER_STUCK_TIMEOUT_SECONDS = env_float("FACTORY_WORKER_TIMEOUT", 300.0, minimum=60.0)
SQLITE_TIMEOUT_SECONDS = env_float("FACTORY_SQLITE_TIMEOUT_SECONDS", 30.0, minimum=1.0)
SQLITE_BUSY_TIMEOUT_MS = env_int("FACTORY_SQLITE_BUSY_TIMEOUT_MS", 30000, minimum=1000)
QWEN_DECOMPOSE_TIMEOUT_SECONDS = env_int("FACTORY_QWEN_DECOMPOSE_TIMEOUT", 300, minimum=1)
QWEN_FIX_TIMEOUT_SECONDS = env_int("FACTORY_QWEN_FIX_TIMEOUT", 120, minimum=1)
ORCHESTRATOR_TICK_INTERVAL_SECONDS = env_float("FACTORY_TICK_INTERVAL", 3.0, minimum=0.2)

from .models import EventType, Severity

if TYPE_CHECKING:  # pragma: no cover
    from .logging import FactoryLogger


class AccountExhaustedError(Exception):
    """Все аккаунты исчерпали лимит."""


def _parse_iso_utc(s: str) -> datetime | None:
    if not s:
        return None
    try:
        t = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


class AccountManager:
    """
    Управляет ротацией API-аккаунтов (Qwen Code CLI: до 9 слотов в env, обычно 3).

    1. Снимает истёкшие cooldown (cooling_down → active).
    2. Выбирает аккаунт с availability=available и remaining>0.
    3. При ответе CLI «лимит» вызывайте mark_rate_limited() — временная блокировка слота.
    4. Если все слоты недоступны — AccountExhaustedError и событие account.pool_exhausted.
    """

    def __init__(self, conn: sqlite3.Connection, logger: Any):
        self.conn = conn
        self.logger = logger
        self._lock = threading.Lock()

    def _expire_cooldowns(self) -> None:
        now = datetime.now(timezone.utc)
        rows = self.conn.execute(
            "SELECT id, cooldown_until FROM api_accounts WHERE account_status = 'cooling_down'"
        ).fetchall()
        for row in rows:
            until = _parse_iso_utc(row["cooldown_until"] or "")
            if until is None or until > now:
                continue
            aid = row["id"]
            prev_until = row["cooldown_until"]
            self.conn.execute(
                """
                UPDATE api_accounts
                SET account_status = 'active', cooldown_until = NULL, last_error = NULL
                WHERE id = ?
                """,
                (aid,),
            )
            self.logger.log(
                EventType.ACCOUNT_RESTORED,
                "account",
                aid,
                "Cooldown истёк, слот снова available",
                severity=Severity.INFO,
                account_id=aid,
                payload={
                    "account_id": aid,
                    "previous_cooldown_until": prev_until,
                    "restored_at": now.isoformat(),
                    "next_available_after": now.isoformat(),
                    "sub": "cooldown_expired",
                },
                tags=["account", "qwen_code_cli"],
            )

    def mark_rate_limited(
        self,
        account_id: str,
        error: str,
        *,
        run_id: str | None = None,
        work_item_id: str | None = None,
        cooldown_seconds: int | None = None,
    ) -> None:
        """Ответ Qwen CLI: 429 / quota / traffic — слот уходит в cooling_down, событие account.rate_limited."""
        sec = cooldown_seconds
        if sec is None:
            sec = int(os.environ.get("FACTORY_ACCOUNT_COOLDOWN_SECONDS", "120"))
        until = (datetime.now(timezone.utc) + timedelta(seconds=max(1, sec))).isoformat()
        with self._lock:
            self.conn.execute(
                """
                UPDATE api_accounts
                SET account_status = 'cooling_down', last_error = ?, cooldown_until = ?
                WHERE id = ?
                """,
                (error[:4000], until, account_id),
            )
        self.logger.log(
            EventType.ACCOUNT_RATE_LIMITED,
            "account",
            account_id,
            f"Qwen CLI лимит: {error[:200]}",
            severity=Severity.WARN,
            run_id=run_id,
            work_item_id=work_item_id,
            account_id=account_id,
            payload={
                "cooldown_until": until,
                "error": error[:4000],
                "sub": "rate_limited",
            },
            tags=["rate_limit", "qwen_code_cli"],
        )
        self.logger.log(
            EventType.ACCOUNT_MARKED_COOLING_DOWN,
            "account",
            account_id,
            f"Слот переведён в cooling_down до {until}",
            severity=Severity.INFO,
            run_id=run_id,
            work_item_id=work_item_id,
            account_id=account_id,
            payload={
                "account_id": account_id,
                "cooldown_until": until,
                "next_available_after": until,
                "sub": "marked_cooling_down",
            },
            tags=["account", "cooldown", "qwen_code_cli"],
        )

    def get_active_account(self) -> dict:
        """Возвращает аккаунт, у которого есть запас, или бросает исключение."""
        with self._lock:
            self._expire_cooldowns()
            rows = self.conn.execute(
                """
                SELECT * FROM v_api_usage_today
                WHERE active = 1
                ORDER BY
                    CASE availability
                        WHEN 'available' THEN 0
                        WHEN 'cooling_down' THEN 1
                        WHEN 'exhausted' THEN 2
                        ELSE 3
                    END,
                    remaining DESC
                """
            ).fetchall()

            if not rows:
                raise AccountExhaustedError("Нет активных аккаунтов")

            for row in rows:
                if row["availability"] == "available" and row["remaining"] > 0:
                    current = self._get_current_account_id()
                    if current != row["account_id"]:
                        self._switch_account(current, row["account_id"])
                    return dict(row)

            reset_at = self._next_reset_time()
            self.logger.log(
                EventType.ACCOUNT_POOL_EXHAUSTED,
                "system",
                "account_manager",
                f"Все {len(rows)} аккаунтов недоступны (лимит/cooldown). Сброс суточного квотирования ожидается к {reset_at}",
                severity=Severity.WARN,
                payload={
                    "accounts": [dict(r) for r in rows],
                    "reset_at": reset_at,
                    "sub": "all_accounts_exhausted",
                },
                tags=["rate_limit", "pause", "qwen_code_cli"],
            )
            raise AccountExhaustedError(
                f"Все аккаунты исчерпали лимит или в cooldown. Сброс квот: {reset_at}"
            )

    def record_usage(
        self,
        account_id: str,
        run_id: str = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        model_name: str = None,
        latency_ms: int = None,
        error: str = None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO api_usage
                (account_id, run_id, tokens_in, tokens_out, model_name, latency_ms, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (account_id, run_id, tokens_in, tokens_out, model_name, latency_ms, error),
        )
        self.conn.execute(
            "UPDATE api_accounts SET last_used_at = ? WHERE id = ?",
            (now, account_id),
        )

    def get_usage_summary(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM v_api_usage_today").fetchall()
        return [dict(r) for r in rows]

    def get_remaining_total(self) -> int:
        rows = self.get_usage_summary()
        return sum(r["remaining"] for r in rows if r["active"])

    def _get_current_account_id(self) -> str:
        row = self.conn.execute(
            "SELECT value FROM system_state WHERE key = 'active_account_id'"
        ).fetchone()
        return row["value"] if row else ACCOUNTS[0]["id"]

    def _switch_account(self, old_id: str, new_id: str):
        self.conn.execute(
            "UPDATE system_state SET value = ? WHERE key = 'active_account_id'",
            (new_id,),
        )
        self.logger.log(
            EventType.TASK_STATUS_CHANGED,
            "system",
            "account_manager",
            f"Смена аккаунта: {old_id} -> {new_id}",
            severity=Severity.INFO,
            payload={"old_account": old_id, "new_account": new_id, "sub": "account_switched"},
            tags=["rate_limit", "account_switch"],
        )

    def _next_reset_time(self) -> str:
        now = datetime.now(timezone.utc)
        tomorrow = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return tomorrow.isoformat()
