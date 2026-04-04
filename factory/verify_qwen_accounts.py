"""
Проверка трёх OAuth-слотов и ротации AccountManager + smoke qwen по каждому токену.

Запуск из каталога ``Fabrika2.0/proekt``::

    python -m factory.verify_qwen_accounts

Ключи берутся из ``<корень репозитория>/.qwen/oauth_creds.json`` (+ _2, _3), не из устаревшего кэша ``.env``.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROEKT = Path(__file__).resolve().parent.parent
REPO = PROEKT.parent.parent

_LOG = logging.getLogger(__name__)


def _inject_oauth_keys_from_repo() -> None:
    """Принудительно выставить FACTORY_API_KEY_1..3 из JSON (игнор .env)."""
    for i in range(1, 10):
        os.environ.pop(f"FACTORY_API_KEY_{i}", None)
    qd = Path(os.environ.get("FACTORY_QWEN_OAUTH_DIR") or (REPO / ".qwen"))
    mapping = [
        (1, "oauth_creds.json", "Alpha"),
        (2, "oauth_creds_2.json", "Beta"),
        (3, "oauth_creds_3.json", "Gamma"),
    ]
    for idx, fname, name in mapping:
        fp = qd / fname
        if not fp.is_file():
            raise FileNotFoundError(f"Нет файла: {fp}")
        data = json.loads(fp.read_text(encoding="utf-8"))
        tok = (data.get("access_token") or "").strip()
        if not tok:
            raise ValueError(f"Нет access_token в {fp}")
        os.environ[f"FACTORY_API_KEY_{idx}"] = tok
        os.environ[f"FACTORY_API_NAME_{idx}"] = name
        os.environ[f"FACTORY_API_LIMIT_{idx}"] = os.environ.get(
            f"FACTORY_API_LIMIT_{idx}", "3000"
        )


def _reload_config():
    import factory.config as cfg

    importlib.reload(cfg)
    return cfg


def run_account_manager_rotation() -> list[str]:
    """Три раза подряд: взять слот → пометить cooling_down → следующий вызов — другой слот."""
    from factory.composition import wire

    fd, raw = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    p = Path(raw)
    f = None
    try:
        f = wire(p)
        am = f["accounts"]
        seen: list[str] = []
        for i in range(3):
            row = am.get_active_account()
            aid = row["account_id"]
            seen.append(aid)
            am.mark_rate_limited(
                aid, f"verify_rotation_{i}", cooldown_seconds=7200
            )
        return seen
    finally:
        if f is not None:
            f["conn"].close()
        p.unlink(missing_ok=True)


def run_qwen_smoke(api_key: str, key_env: str) -> tuple[int, str]:
    exe = shutil.which("qwen")
    if not exe:
        return -1, "qwen not on PATH"
    env = os.environ.copy()
    env[key_env] = api_key
    try:
        r = subprocess.run(
            [exe, "--approval-mode=yolo", "-p", "Reply with exactly: OK"],
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(PROEKT),
        )
        tail = ((r.stderr or "") + "\n" + (r.stdout or ""))[-2500:]
        return r.returncode, tail
    except subprocess.TimeoutExpired:
        return -99, "timeout 180s"


def main() -> int:
    os.chdir(PROEKT)
    try:
        _inject_oauth_keys_from_repo()
    except (OSError, ValueError, json.JSONDecodeError) as e:
        _LOG.error("FAIL: %s", e)
        return 1

    cfg = _reload_config()
    ids_cfg = [a["id"] for a in cfg.ACCOUNTS]
    _LOG.info("=== Конфиг: %s аккаунтов ===\n%s", len(cfg.ACCOUNTS), ids_cfg)
    if len(cfg.ACCOUNTS) != 3:
        _LOG.error("FAIL: нужны ровно 3 аккаунта.")
        return 1

    _LOG.info("=== AccountManager: three distinct slots (rate_limited -> next id) ===")
    seen = run_account_manager_rotation()
    _LOG.info("цепочка: %s", seen)
    if len(set(seen)) != 3:
        _LOG.error("FAIL: ожидались 3 разных account_id.")
        return 1
    _LOG.info("OK.")

    key_env = (os.environ.get("FACTORY_QWEN_SUBPROCESS_KEY_ENV") or "").strip() or "OPENAI_API_KEY"
    _LOG.info("=== Smoke qwen (по одному вызову на токен, env %s) ===", key_env)

    fd, raw = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    p = Path(raw)
    f = None
    try:
        from factory.composition import wire

        f = wire(p)
        conn = f["conn"]
        rows = conn.execute(
            "SELECT id, name, api_key FROM api_accounts ORDER BY priority"
        ).fetchall()
        alts = ["OPENAI_API_KEY", "QWEN_API_KEY", "ANTHROPIC_API_KEY"]

        for row in rows:
            aid, name, key = row["id"], row["name"], row["api_key"]
            _LOG.info("-- %s (%s)", name, aid)
            code, tail = run_qwen_smoke(key, key_env)
            if code == 0:
                _LOG.info("OK (exit 0)")
                continue
            _LOG.warning("exit %s, try other env names for API key...", code)
            ok = False
            for alt in alts:
                if alt == key_env:
                    continue
                c2, t2 = run_qwen_smoke(key, alt)
                if c2 == 0:
                    _LOG.info("OK with env %s (exit 0)", alt)
                    ok = True
                    break
            if not ok:
                _LOG.error("FAIL:\n%s", tail)
                return 1
    finally:
        if f is not None:
            f["conn"].close()
        p.unlink(missing_ok=True)

    _LOG.info("=== All checks passed ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
