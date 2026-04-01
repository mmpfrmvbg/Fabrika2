"""
Вызов Qwen Code CLI с ротацией аккаунтов.

Retry при 429/quota — только здесь; оркестратор и FSM видят итог (forge_completed / forge_failed).
См. ``AccountManager.mark_rate_limited`` и ``EventType.ACCOUNT_RATE_LIMITED``.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, field
from typing import Sequence

from .config import AccountExhaustedError, AccountManager
from .logging import FactoryLogger
from .models import EventType, Severity

# Подстроки stderr/stdout, по которым считаем ответ «лимит» (CLI может отличаться).
# E2E: один раз за процесс — «успех без правки файлов» (см. FACTORY_QWEN_E2E_SIMULATE_OK_NO_FILE_CHANGE).
_E2E_OK_NO_ARTIFACT_USED: bool = False


def reset_e2e_qwen_simulation_hooks() -> None:
    """Сброс E2E-флагов (для тестов или повторного прогона в одном процессе)."""
    global _E2E_OK_NO_ARTIFACT_USED
    _E2E_OK_NO_ARTIFACT_USED = False


_RATE_MARKERS: Sequence[str] = (
    "429",
    "rate limit",
    "rate_limit",
    "too many requests",
    "quota",
    "resource exhausted",
    "traffic",
    "exhausted",
    "blk",
    "quota exceeded",
)


@dataclass
class ForgeResult:
    """Результат исполнения атома через Qwen CLI (несколько аккаунтов при ротации)."""

    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    # AccountExhaustedError при get_active_account — все слоты недоступны
    exhausted_accounts: bool = False
    # Исчерпан FACTORY_QWEN_MAX_ACCOUNT_TRIES без успешного вызова
    max_tries_reached: bool = False
    error_message: str | None = None
    accounts_tried: list[str] = field(default_factory=list)


def looks_rate_limited(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    return any(m in t for m in _RATE_MARKERS)


def _env_truthy(name: str) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _env_qwen_dry_run() -> bool:
    """
    Если ``FACTORY_QWEN_DRY_RUN`` не задан — ``True`` (без subprocess, успех).
    Явно ``0`` / ``false`` / ``no`` / ``off`` — реальный вызов CLI.
    """
    raw = os.environ.get("FACTORY_QWEN_DRY_RUN")
    if raw is None:
        return True
    s = raw.strip().lower()
    if s in ("0", "false", "no", "off"):
        return False
    if s in ("1", "true", "yes", "on") or s == "":
        return True
    return True


def _fetch_api_key(conn: sqlite3.Connection, account_id: str) -> str:
    row = conn.execute(
        "SELECT api_key FROM api_accounts WHERE id = ?", (account_id,)
    ).fetchone()
    return (row["api_key"] if row else "") or ""


def _build_prompt(
    *,
    work_item_id: str,
    title: str,
    description: str,
    system_prompt: str | None,
) -> str:
    parts = [
        (system_prompt or "").strip(),
        "",
        f"work_item_id: {work_item_id}",
        f"title: {title}",
        f"description:\n{description or '(none)'}",
    ]
    return "\n".join(p for p in parts if p is not None).strip()


def _subprocess_env(api_key: str) -> dict[str, str]:
    env = os.environ.copy()
    key_var = (os.environ.get("FACTORY_QWEN_SUBPROCESS_KEY_ENV") or "").strip()
    if key_var and api_key:
        env[key_var] = api_key
    return env


def _resolve_qwen_executable(bin_name: str) -> str:
    """Windows: ``qwen`` без расширения часто не находится — используем ``shutil.which``."""
    w = shutil.which(bin_name)
    return w if w else bin_name


def _extra_has_yolo_or_approval(extra: list[str]) -> bool:
    """Уже задано автоодобрение инструментов (иначе в subprocess правки к диску не доходят)."""
    for t in extra:
        if t in ("-y", "--yolo"):
            return True
        if t == "--approval-mode" or t.startswith("--approval-mode="):
            return True
    return False


def _build_extra_argv() -> list[str]:
    """``FACTORY_QWEN_EXTRA_ARGS`` + дефолты для неинтерактивного forge (yolo + несколько ходов + channel)."""
    extra = shlex.split(os.environ.get("FACTORY_QWEN_EXTRA_ARGS", ""))
    joined = " ".join(extra)

    # Без этого CLI часто делает one-shot ответ без вызова edit/write tools.
    if "--max-session-turns" not in joined:
        turns = (os.environ.get("FACTORY_QWEN_MAX_SESSION_TURNS") or "25").strip()
        if turns:
            extra.extend(["--max-session-turns", turns])

    # Иначе approval-mode=default ждёт подтверждения, которого нет в pipe — файлы не пишутся.
    if not _extra_has_yolo_or_approval(extra):
        extra.extend(["--approval-mode", "yolo"])

    if "--channel" not in joined:
        ch = (os.environ.get("FACTORY_QWEN_CHANNEL") or "").strip()
        if ch:
            extra.extend(["--channel", ch])
    return extra


def _env_qwen_debug() -> bool:
    return os.environ.get("FACTORY_QWEN_DEBUG", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _log_qwen_cli_debug(
    *,
    cmd: list[str],
    cwd: str | None,
    via: str,
    prompt_len: int,
    stdout: str,
    stderr: str,
    exit_code: int | None,
) -> None:
    """В stderr/лог: argv (без секретов в значениях — только список), cwd, transport, хвосты вывода."""
    if not _env_qwen_debug():
        return
    log = logging.getLogger("factory.qwen_cli")
    safe = " ".join(shlex.quote(str(c)) for c in cmd)
    out_h = (stdout or "")[:4000]
    err_h = (stderr or "")[:4000]
    log.warning(
        "QWEN_DEBUG via=%s cwd=%s prompt_len=%s exit=%s\ncmd: %s\n--- stdout (head) ---\n%s\n--- stderr (head) ---\n%s",
        via,
        cwd or "(inherit)",
        prompt_len,
        exit_code,
        safe,
        out_h,
        err_h,
    )


def _qwen_command(prompt: str) -> tuple[list[str], str | bytes | None, str]:
    """
    argv, stdin payload или None, и метка transport (``argv`` / ``stdin``).
    """
    bin_raw = (os.environ.get("FACTORY_QWEN_BIN") or os.environ.get("QWENBIN") or "qwen").strip()
    bin_name = _resolve_qwen_executable(bin_raw)
    extra = _build_extra_argv()
    # stdin: длинные forge-промпты; argv на Windows упирается в лимит длины командной строки.
    via = (os.environ.get("FACTORY_QWEN_PROMPT_VIA") or "stdin").strip().lower()
    if via == "stdin":
        cmd = [bin_name, *extra]
        if os.environ.get("FACTORY_QWEN_STDIN_DASH_P", "1") not in ("0", "false"):
            cmd.append("-p")
            cmd.append("-")
        return cmd, (prompt.encode("utf-8") if prompt else b""), "stdin"
    cmd = [bin_name, *extra, "-p", prompt]
    return cmd, None, "argv"


def run_qwen_cli(
    *,
    conn: sqlite3.Connection,
    account_manager: AccountManager,
    logger: FactoryLogger,
    work_item_id: str,
    run_id: str | None = None,
    title: str,
    description: str = "",
    system_prompt: str | None = None,
    full_prompt: str | None = None,
    cwd: str | None = None,
) -> ForgeResult:
    """
    Цикл: выбрать аккаунт → subprocess → при rate limit пометить слот и взять следующий;
    при ``AccountExhaustedError`` — ``exhausted_accounts``;
    при прочей ошибке CLI — один выход без ротации на другой аккаунт.

    Окружение:
    - ``FACTORY_QWEN_DRY_RUN`` не задан или ``1`` — без subprocess, успех (дефолт безопасный).
    - ``FACTORY_QWEN_DRY_RUN=0`` — реальный subprocess.
    - ``FACTORY_QWEN_SUBPROCESS_KEY_ENV`` — имя env для ключа из БД (иначе ключ не подставляется).
    - ``FACTORY_QWEN_MAX_ACCOUNT_TRIES`` — максимум итераций цикла (по умолчанию 12).
    - ``FACTORY_QWEN_TIMEOUT_SEC`` — таймаут subprocess (по умолчанию 600).
    - ``full_prompt`` — если задан, подставляется вместо сборки из title/description/system_prompt (песочница + кузница).
    - ``cwd`` — рабочая директория subprocess (песочница forge).
    - ``FACTORY_QWEN_PROMPT_VIA`` — по умолчанию ``stdin`` (длинные промпты; ``argv`` — ``-p`` в argv, риск лимита длины на Windows).
    - ``FACTORY_QWEN_MAX_SESSION_TURNS`` — ходы агента (по умолчанию ``25``, если не задано в ``FACTORY_QWEN_EXTRA_ARGS``).
    - Без ``--approval-mode`` / ``-y`` в ``FACTORY_QWEN_EXTRA_ARGS`` подставляется ``--approval-mode yolo``, иначе в неинтерактивном режиме инструменты записи не выполняются.
    - ``FACTORY_QWEN_CHANNEL`` — например ``CI`` (передаётся как ``--channel``).
    - ``FACTORY_QWEN_DEBUG=1`` — в лог ``factory.qwen_cli``: cmd, cwd, via, первые строки stdout/stderr (без ключей).
    - **E2E-only:** ``FACTORY_QWEN_E2E_SIMULATE_RATE_LIMIT_ON_FIRST_CALL`` — первый заход в цикл ротации без subprocess, синтетический 429, затем реальный CLI.
    - **E2E-only:** ``FACTORY_QWEN_E2E_SIMULATE_OK_NO_FILE_CHANGE`` — один раз за процесс: успех без subprocess (для ``--e2e-qwen-wet-forge-no-artifact``). Сброс: ``reset_e2e_qwen_simulation_hooks()``.
    """
    dry = _env_qwen_dry_run()
    if full_prompt is not None:
        prompt_for_len = full_prompt.strip()
    else:
        prompt_for_len = _build_prompt(
            work_item_id=work_item_id,
            title=title,
            description=description,
            system_prompt=system_prompt,
        )
    _qwen_log_entity_type = "run" if run_id else "work_item"
    _qwen_log_entity_id = run_id if run_id else work_item_id
    logger.log(
        EventType.QWEN_RUN_INVOCATION,
        _qwen_log_entity_type,
        _qwen_log_entity_id,
        "Qwen CLI runner invoked" + (" (dry run)" if dry else ""),
        work_item_id=work_item_id,
        run_id=run_id,
        severity=Severity.INFO,
        payload={"dry_run": dry},
        tags=["qwen", "runner"],
    )
    if dry:
        cmd_dry, _stdin_dry, via_dry = _qwen_command(prompt_for_len)
        argv_dry = [str(x) for x in cmd_dry]
        logger.log(
            EventType.FORGE_CLI_INVOKED,
            "work_item",
            work_item_id,
            "forge_cli_invoked (dry)",
            work_item_id=work_item_id,
            run_id=run_id,
            severity=Severity.INFO,
            payload={
                "dry_run": True,
                "argv": argv_dry,
                "via": via_dry,
                "account_id": None,
                "account_name": None,
            },
            tags=["forge", "audit"],
        )
        out_dry = "FACTORY_QWEN_DRY_RUN=1"
        logger.log(
            EventType.FORGE_CLI_COMPLETED,
            "work_item",
            work_item_id,
            "forge_cli_completed (dry)",
            work_item_id=work_item_id,
            run_id=run_id,
            severity=Severity.INFO,
            payload={
                "exit_code": 0,
                "stdout_len": len(out_dry),
                "stderr_len": 0,
                "rate_limit_markers": False,
                "dry_run": True,
            },
            tags=["forge", "audit"],
        )
        return ForgeResult(
            ok=True,
            stdout=out_dry,
            stderr="",
            exit_code=0,
        )

    max_iter = max(1, int(os.environ.get("FACTORY_QWEN_MAX_ACCOUNT_TRIES", "12")))
    timeout = max(1, int(os.environ.get("FACTORY_QWEN_TIMEOUT_SEC", "600")))
    if full_prompt is not None:
        prompt = full_prompt.strip()
    else:
        prompt = _build_prompt(
            work_item_id=work_item_id,
            title=title,
            description=description,
            system_prompt=system_prompt,
        )

    accounts_tried: list[str] = []
    e2e_sim_rl_first = _env_truthy("FACTORY_QWEN_E2E_SIMULATE_RATE_LIMIT_ON_FIRST_CALL")
    e2e_rl_sim_consumed = False

    for _ in range(max_iter):
        try:
            account = account_manager.get_active_account()
        except AccountExhaustedError as e:
            return ForgeResult(
                ok=False,
                exhausted_accounts=True,
                error_message=str(e),
                accounts_tried=list(accounts_tried),
            )

        aid = account["account_id"]
        accounts_tried.append(aid)

        # E2E: один раз за процесс — успех без вызова subprocess (для forge_no_artifact).
        global _E2E_OK_NO_ARTIFACT_USED
        if _env_truthy("FACTORY_QWEN_E2E_SIMULATE_OK_NO_FILE_CHANGE") and not _E2E_OK_NO_ARTIFACT_USED:
            _E2E_OK_NO_ARTIFACT_USED = True
            cmd_e2e, _s_e2e, via_e2e = _qwen_command(prompt)
            argv_e2e = [str(x) for x in cmd_e2e]
            logger.log(
                EventType.FORGE_CLI_INVOKED,
                "work_item",
                work_item_id,
                "forge_cli_invoked (e2e simulate ok no file change)",
                work_item_id=work_item_id,
                run_id=run_id,
                account_id=aid,
                payload={
                    "dry_run": False,
                    "argv": argv_e2e,
                    "via": via_e2e,
                    "account_id": aid,
                    "account_name": account.get("account_name"),
                    "e2e_simulate": "ok_no_file_change",
                },
                tags=["forge", "audit"],
            )
            out_e2e = "E2E_FACTORY_QWEN_E2E_SIMULATE_OK_NO_FILE_CHANGE=1"
            logger.log(
                EventType.FORGE_CLI_COMPLETED,
                "work_item",
                work_item_id,
                "forge_cli_completed (e2e simulate)",
                work_item_id=work_item_id,
                run_id=run_id,
                account_id=aid,
                payload={
                    "exit_code": 0,
                    "stdout_len": len(out_e2e),
                    "stderr_len": 0,
                    "rate_limit_markers": False,
                    "e2e_simulate": "ok_no_file_change",
                },
                tags=["forge", "audit"],
            )
            return ForgeResult(
                ok=True,
                stdout=out_e2e,
                stderr="",
                exit_code=0,
                accounts_tried=list(accounts_tried),
            )

        api_key = _fetch_api_key(conn, aid)
        env = _subprocess_env(api_key)
        cmd, stdin_payload, via = _qwen_command(prompt)

        # E2E only: первый «вызов» — синтетический rate limit, без subprocess; второй — реальный qwen.
        if e2e_sim_rl_first and not e2e_rl_sim_consumed:
            e2e_rl_sim_consumed = True
            out = ""
            err = "429 too many requests (e2e simulated rate limit)"
            _log_qwen_cli_debug(
                cmd=cmd,
                cwd=cwd,
                via=via,
                prompt_len=len(prompt),
                stdout=out,
                stderr=err,
                exit_code=1,
            )
            combined = f"{out}\n{err}"
            if looks_rate_limited(combined):
                account_manager.mark_rate_limited(
                    aid,
                    err,
                    run_id=run_id,
                    work_item_id=work_item_id,
                )
                continue
            return ForgeResult(
                ok=False,
                stdout=out,
                stderr=err,
                exit_code=1,
                error_message=err,
                accounts_tried=list(accounts_tried),
            )

        logger.log(
            EventType.ACCOUNT_SELECTED,
            "account",
            aid,
            f"account.selected {account.get('account_name', aid)}",
            run_id=run_id,
            work_item_id=work_item_id,
            account_id=aid,
            payload={
                "account_id": aid,
                "account_name": account.get("account_name"),
            },
            tags=["account"],
        )
        argv_wet = [str(x) for x in cmd]
        logger.log(
            EventType.FORGE_CLI_INVOKED,
            "work_item",
            work_item_id,
            "forge_cli_invoked (subprocess)",
            work_item_id=work_item_id,
            run_id=run_id,
            account_id=aid,
            payload={
                "dry_run": False,
                "argv": argv_wet,
                "via": via,
                "account_id": aid,
                "account_name": account.get("account_name"),
            },
            tags=["forge", "audit"],
        )

        try:
            r = subprocess.run(
                cmd,
                input=stdin_payload,
                capture_output=True,
                timeout=timeout,
                env=env,
                cwd=cwd or None,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.log(
                EventType.FORGE_CLI_COMPLETED,
                "work_item",
                work_item_id,
                "forge_cli_completed (timeout)",
                work_item_id=work_item_id,
                run_id=run_id,
                account_id=aid,
                severity=Severity.WARN,
                payload={
                    "exit_code": -1,
                    "stdout_len": 0,
                    "stderr_len": 0,
                    "rate_limit_markers": False,
                    "error": f"timeout ({timeout}s)",
                },
                tags=["forge", "audit"],
            )
            return ForgeResult(
                ok=False,
                error_message=f"Qwen CLI timeout ({timeout}s)",
                accounts_tried=list(accounts_tried),
                exit_code=-1,
            )
        except OSError as e:
            logger.log(
                EventType.FORGE_CLI_COMPLETED,
                "work_item",
                work_item_id,
                "forge_cli_completed (spawn error)",
                work_item_id=work_item_id,
                run_id=run_id,
                account_id=aid,
                severity=Severity.ERROR,
                payload={
                    "exit_code": None,
                    "stdout_len": 0,
                    "stderr_len": 0,
                    "rate_limit_markers": False,
                    "error": str(e),
                },
                tags=["forge", "audit"],
            )
            return ForgeResult(
                ok=False,
                error_message=f"Qwen CLI spawn failed: {e}",
                accounts_tried=list(accounts_tried),
            )

        out = (r.stdout or b"").decode("utf-8", errors="replace")
        err = (r.stderr or b"").decode("utf-8", errors="replace")
        _log_qwen_cli_debug(
            cmd=cmd,
            cwd=cwd,
            via=via,
            prompt_len=len(prompt),
            stdout=out,
            stderr=err,
            exit_code=r.returncode,
        )
        combined = f"{out}\n{err}"
        rl = looks_rate_limited(combined)
        logger.log(
            EventType.FORGE_CLI_COMPLETED,
            "work_item",
            work_item_id,
            "forge_cli_completed",
            work_item_id=work_item_id,
            run_id=run_id,
            account_id=aid,
            payload={
                "exit_code": r.returncode,
                "stdout_len": len(out or ""),
                "stderr_len": len(err or ""),
                "rate_limit_markers": rl,
            },
            tags=["forge", "audit"],
        )

        if r.returncode == 0 and not rl:
            account_manager.record_usage(
                aid,
                run_id=run_id,
                tokens_in=0,
                tokens_out=0,
                model_name="qwen_code_cli",
            )
            return ForgeResult(
                ok=True,
                stdout=out,
                stderr=err,
                exit_code=r.returncode,
                accounts_tried=list(accounts_tried),
            )

        if rl:
            account_manager.mark_rate_limited(
                aid,
                err or out or "rate_limited",
                run_id=run_id,
                work_item_id=work_item_id,
            )
            continue

        return ForgeResult(
            ok=False,
            stdout=out,
            stderr=err,
            exit_code=r.returncode,
            error_message=(err or out or f"exit {r.returncode}")[:4000],
            accounts_tried=list(accounts_tried),
        )

    return ForgeResult(
        ok=False,
        max_tries_reached=True,
        error_message=f"Исчерпан лимит итераций ({max_iter}) без успешного вызова",
        accounts_tried=list(accounts_tried),
    )
