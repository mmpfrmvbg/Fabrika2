"""
Песочница forge: копия файлов атома, захват diff после Qwen, запись ``file_changes`` / ``run_steps``.

Рабочая копия репозитория: ``FACTORY_WORKSPACE_ROOT`` (по умолчанию текущий каталог процесса).
"""

from __future__ import annotations

import difflib
import hashlib
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .config import FACTORY_WORKSPACE_ROOT
from .db import gen_id
from .models import EventType, Severity

if TYPE_CHECKING:
    from .logging import FactoryLogger


def workspace_root() -> Path:
    if FACTORY_WORKSPACE_ROOT:
        return Path(FACTORY_WORKSPACE_ROOT).resolve()
    return Path.cwd().resolve()


def safe_path_under_workspace(base: Path, rel: str) -> Path:
    """
    Разрешает относительный путь внутри ``base``; запрещает ``..``, абсолютные пути и выход за границу.
    Используется для путей из БД / LLM при записи в workspace или песочницу.
    """
    s = (rel or "").replace("\\", "/").strip()
    if not s:
        raise ValueError("empty path")
    if os.path.isabs(s) or Path(s).is_absolute():
        raise ValueError(f"absolute path not allowed: {rel!r}")
    base_r = base.resolve()
    candidate = (base_r / s).resolve()
    try:
        candidate.relative_to(base_r)
    except ValueError as e:
        raise ValueError(f"Path traversal blocked: {rel!r}") from e
    return candidate


def _sha256(data: bytes | None) -> str | None:
    if data is None:
        return None
    return hashlib.sha256(data).hexdigest()


def _rel_key(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


@dataclass
class SandboxContext:
    """Корень песочницы и снимок содержимого до вызова CLI (относительный путь → байты или None)."""

    root: Path
    baseline: dict[str, bytes | None]
    # path (posix) → исходный intent в БД был 'modify', применён fallback create
    intent_overrides: dict[str, str] = field(default_factory=dict)


def resolve_effective_work_item_files(
    conn: sqlite3.Connection,
    work_item_id: str,
    repo_root: Path,
    *,
    logger: FactoryLogger | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Строки work_item_files с учётом семантики modify: отсутствующий файл → effective intent=create,
    intent_override='modify', событие forge.modify_missing_file (если передан logger).
    """
    rows = conn.execute(
        """
        SELECT path, intent, description, required
        FROM work_item_files
        WHERE work_item_id = ?
        ORDER BY path
        """,
        (work_item_id,),
    ).fetchall()
    rr = repo_root.resolve()
    out: list[dict[str, Any]] = []
    for r in rows:
        rel = (r["path"] or "").replace("\\", "/").strip()
        if not rel:
            continue
        intent = (r["intent"] or "").lower()
        effective_intent = intent
        intent_override: str | None = None
        if intent == "modify":
            try:
                src = safe_path_under_workspace(rr, rel)
            except ValueError:
                out.append(
                    {
                        "path": r["path"],
                        "intent": intent,
                        "description": r["description"],
                        "required": r["required"],
                        "intent_override": None,
                    }
                )
                continue
            if not src.is_file():
                effective_intent = "create"
                intent_override = "modify"
                if logger is not None:
                    logger.log(
                        EventType.FORGE_MODIFY_MISSING_FILE,
                        "work_item",
                        work_item_id,
                        f"File {rel} not found, falling back to create",
                        severity=Severity.WARN,
                        work_item_id=work_item_id,
                        run_id=run_id,
                        payload={"path": rel, "detail": "forge.modify_missing_file"},
                        tags=["forge", "audit"],
                    )
        out.append(
            {
                "path": r["path"],
                "intent": effective_intent,
                "description": r["description"],
                "required": r["required"],
                "intent_override": intent_override,
            }
        )
    return out


def prepare_sandbox(
    conn: sqlite3.Connection,
    work_item_id: str,
    run_id: str,
    *,
    repo_root: Path | None = None,
    effective_files: list[dict[str, Any]] | None = None,
) -> SandboxContext:
    """
    Временная директория; копируются файлы с ``intent`` read/modify из репозитория;
    для create — только родительские каталоги, baseline[path]=None.
    """
    rr = repo_root or workspace_root()
    root = Path(
        tempfile.mkdtemp(prefix=f"forge_{run_id}_", dir=os.environ.get("FACTORY_FORGE_TMP_PARENT") or None)
    )
    baseline: dict[str, bytes | None] = {}
    intent_overrides: dict[str, str] = {}

    rows = effective_files
    if rows is None:
        rows = resolve_effective_work_item_files(
            conn, work_item_id, rr, logger=None, run_id=None
        )

    for r in rows:
        rel = (r["path"] or "").replace("\\", "/").strip()
        if not rel:
            continue
        intent = (r["intent"] or "").lower()
        if r.get("intent_override") == "modify":
            intent_overrides[rel] = "modify"
        try:
            dest = safe_path_under_workspace(root, rel)
        except ValueError:
            continue

        if intent == "create":
            dest.parent.mkdir(parents=True, exist_ok=True)
            baseline[rel] = None
            continue

        if intent in ("read", "modify"):
            try:
                src = safe_path_under_workspace(rr, rel)
            except ValueError:
                baseline[rel] = None
                dest.parent.mkdir(parents=True, exist_ok=True)
                continue
            if src.is_file():
                data = src.read_bytes()
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                baseline[rel] = data
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"")
                baseline[rel] = b""

    conn.execute(
        "UPDATE runs SET sandbox_ref = ? WHERE id = ?",
        (str(root), run_id),
    )
    return SandboxContext(root=root, baseline=baseline, intent_overrides=intent_overrides)


def _walk_files(root: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for p in root.rglob("*"):
        if p.is_file() and ".git" not in p.parts:
            rel = _rel_key(p, root)
            try:
                out[rel] = p.read_bytes()
            except OSError:
                continue
    return out


@dataclass
class CapturedChange:
    path: str
    change_type: str
    old_hash: str | None
    new_hash: str | None
    diff_summary: str
    lines_added: int
    lines_removed: int
    intent_override: str | None = None  # DB intent был modify, фактически create


def declared_modify_paths_without_capture(
    conn: sqlite3.Connection,
    work_item_id: str,
    changes: list[CapturedChange],
    *,
    effective_files: list[dict[str, Any]] | None = None,
) -> list[str]:
    """
    Пути с ``intent=modify`` из ``work_item_files``, для которых нет записи в ``changes``
    (нет отличия от baseline в песочнице после CLI).
    """
    if effective_files is not None:
        modify_paths = [
            (r["path"] or "").replace("\\", "/").strip()
            for r in effective_files
            if (r.get("intent") or "").lower() == "modify"
        ]
    else:
        rows = conn.execute(
            """
            SELECT path FROM work_item_files
            WHERE work_item_id = ? AND LOWER(COALESCE(intent, '')) = 'modify'
            ORDER BY path
            """,
            (work_item_id,),
        ).fetchall()
        modify_paths = [(r["path"] or "").replace("\\", "/").strip() for r in rows]
    captured = {c.path.replace("\\", "/") for c in changes}
    missing: list[str] = []
    for rel in modify_paths:
        if not rel:
            continue
        if rel not in captured:
            missing.append(rel)
    return missing


def capture_changes(ctx: SandboxContext) -> list[CapturedChange]:
    """Сравнение baseline с текущим состоянием песочницы; новые файлы — ``created``."""
    current = _walk_files(ctx.root)
    all_rels = sorted(set(ctx.baseline.keys()) | set(current.keys()))
    changes: list[CapturedChange] = []

    for rel in all_rels:
        old_b = ctx.baseline.get(rel)
        new_b = current.get(rel)

        if old_b == new_b:
            continue

        old_h = _sha256(old_b)
        new_h = _sha256(new_b)

        if old_b is None and new_b is not None:
            ct = "created"
        elif old_b is not None and new_b is None:
            ct = "deleted"
        else:
            ct = "modify"

        old_lines = (old_b or b"").decode("utf-8", errors="replace").splitlines()
        new_lines = (new_b or b"").decode("utf-8", errors="replace").splitlines()
        diff = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
                lineterm="",
            )
        )
        diff_text = "\n".join(diff[:400])
        if len(diff) > 400:
            diff_text += "\n... [diff truncated] ..."

        added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))

        io = ctx.intent_overrides.get(rel)
        changes.append(
            CapturedChange(
                path=rel,
                change_type=ct,
                old_hash=old_h,
                new_hash=new_h,
                diff_summary=diff_text[:8000],
                lines_added=added,
                lines_removed=removed,
                intent_override=io,
            )
        )

    return changes


def apply_dry_run_placeholder(
    ctx: SandboxContext,
    conn: sqlite3.Connection,
    work_item_id: str,
    *,
    effective_files: list[dict[str, Any]] | None = None,
) -> None:
    """
    После успешного dry-run Qwen: лёгкое изменение файлов в песочнице,
    чтобы ``capture_changes`` зафиксировал реальный diff (без вызова бинарника).
    """
    rows = effective_files
    if rows is None:
        rows = resolve_effective_work_item_files(
            conn, work_item_id, workspace_root(), logger=None, run_id=None
        )
    rows = [r for r in rows if (r.get("intent") or "").lower() in ("create", "modify", "read")]
    marker = b"\n# forge: FACTORY_QWEN_DRY_RUN placeholder\n"
    for r in rows:
        rel = (r["path"] or "").replace("\\", "/").strip()
        if not rel:
            continue
        try:
            p = safe_path_under_workspace(ctx.root, rel)
        except ValueError:
            continue
        if (r["intent"] or "").lower() == "create":
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.is_file():
                p.write_bytes(b"# created (dry-run)\n")
            else:
                p.write_bytes(p.read_bytes() + marker)
        else:
            if p.is_file():
                p.write_bytes(p.read_bytes() + marker)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(marker.strip() + b"\n")


def persist_captured_changes(
    conn: sqlite3.Connection,
    work_item_id: str,
    run_id: str,
    changes: list[CapturedChange],
) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(file_changes)").fetchall()}
    has_io = "intent_override" in cols
    for ch in changes:
        if has_io:
            conn.execute(
                """
                INSERT INTO file_changes
                    (id, work_item_id, run_id, path, change_type, old_hash, new_hash,
                     diff_summary, lines_added, lines_removed, intent_override)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    gen_id("fc"),
                    work_item_id,
                    run_id,
                    ch.path,
                    ch.change_type,
                    ch.old_hash,
                    ch.new_hash,
                    ch.diff_summary,
                    ch.lines_added,
                    ch.lines_removed,
                    ch.intent_override,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO file_changes
                    (id, work_item_id, run_id, path, change_type, old_hash, new_hash,
                     diff_summary, lines_added, lines_removed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    gen_id("fc"),
                    work_item_id,
                    run_id,
                    ch.path,
                    ch.change_type,
                    ch.old_hash,
                    ch.new_hash,
                    ch.diff_summary,
                    ch.lines_added,
                    ch.lines_removed,
                ),
            )


def apply_sandbox_to_workspace(
    *,
    ctx: SandboxContext,
    changes: list[CapturedChange],
    repo_root: Path | None = None,
) -> None:
    """
    Переносит фактические изменения из песочницы в рабочую копию репозитория (workspace_root).

    Безопасность:
    - принимаем только относительные пути (как ключи diff), запрещаем выход за пределы repo_root
    - для deleted удаляем файл, если он внутри repo_root
    """
    rr = (repo_root or workspace_root()).resolve()
    for ch in changes:
        rel = (ch.path or "").replace("\\", "/").strip()
        if not rel:
            continue
        try:
            src = safe_path_under_workspace(ctx.root, rel)
            dst = safe_path_under_workspace(rr, rel)
        except ValueError as e:
            raise ValueError(f"forge sandbox попытался записать вне workspace: {rel}") from e

        if ch.change_type == "deleted":
            try:
                if dst.is_file():
                    dst.unlink()
            except OSError:
                # если не удалилось — это ошибка записи в workspace
                raise
            continue

        # created / modify: копируем байты из песочницы
        if not src.is_file():
            # если в diff считается изменением, но файла нет — это ошибка
            raise FileNotFoundError(f"forge sandbox missing file for change: {rel}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())


def cleanup_sandbox(ctx: SandboxContext | None) -> None:
    if ctx is None:
        return
    try:
        shutil.rmtree(ctx.root, ignore_errors=True)
    except OSError:
        pass
