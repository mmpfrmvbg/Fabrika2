"""
Сборка текста промпта для Qwen Code CLI из атома (БД) + корень рабочей копии репозитория.

Используется ``forge_worker``; тестируется без вызова Qwen.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .forge_sandbox import resolve_effective_work_item_files

# Лимит на один файл в промпте (символы), чтобы не раздувать контекст.
_MAX_FILE_CHARS = 64_000

FORGE_SYSTEM_PREAMBLE = """You are the Forge (Кузница) coding agent for this factory run.
Work ONLY within the declared file paths below. Do not touch files outside this scope.
Prefer minimal, correct edits. After editing, ensure the code is syntactically valid.
The sandbox working directory is your workspace root for this task.
"""

FORGE_APPLY_INSTRUCTIONS = """
## Mandatory output behavior
You MUST apply all code changes by directly editing the declared files inside the current working directory (sandbox). Use the editor/write tools so the files on disk change — do not describe changes in prose only.

If a file is declared with intent `modify`, you MUST produce an on-disk change for that file (a diff). If the correct functional behavior is already present, apply a minimal non-functional change (e.g., add/adjust a short module docstring) so the pipeline captures an artifact.

If this is a retry and there is "Previous feedback (MUST address)" but you believe the current code already satisfies it, still apply a minimal non-functional change inside a declared `modify` file (e.g., add a short module docstring) so that an artifact/diff is produced and the reviewer/judge can re-evaluate with updated context.

## Mandatory stdout format (so reviewer/judge can verify)
After you finish editing files on disk, print the FULL final contents of every declared `modify` / `create` file.

Use this exact format for each file (repeat for all files):

FILE://<relative/path>
```text
<FULL FILE CONTENTS HERE — NOT A FRAGMENT>
```

Rules:
- Print the entire file, even if it is long (but keep changes minimal).
- Do not print diffs. Print full files.
- If this is a retry, fix ONLY what is asked in "Previous feedback (MUST address)". Do not introduce unrelated refactors.
"""


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 40] + "\n... [truncated] ...\n"


def _file_contents_block(repo_root: Path, rel_path: str) -> tuple[str, bool]:
    """Читает файл относительно repo_root; пустая строка + False если нет файла."""
    p = (repo_root / rel_path).resolve()
    try:
        p.relative_to(repo_root.resolve())
    except ValueError:
        return "", False
    if not p.is_file():
        return "", False
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", False
    return _truncate(raw, _MAX_FILE_CHARS), True


def build_forge_prompt(
    conn: sqlite3.Connection,
    work_item_id: str,
    *,
    repo_root: Path,
    effective_files: list[dict[str, Any]] | None = None,
) -> str:
    """
    Полный текст контекста для Qwen (system-инструкция + файлы + комментарии + решения + задача).

    Файлы подставляются для ``intent`` в ``read``, ``modify`` (текущее состояние из ``repo_root``).
    """
    wi = conn.execute(
        "SELECT id, title, description FROM work_items WHERE id = ?",
        (work_item_id,),
    ).fetchone()
    if not wi:
        return ""

    parts: list[str] = [FORGE_SYSTEM_PREAMBLE.strip(), ""]

    files = effective_files
    if files is None:
        files = resolve_effective_work_item_files(
            conn, work_item_id, repo_root, logger=None, run_id=None
        )

    lines = ["## Declared files (path — intent)"]
    for r in files:
        desc = (r["description"] or "").strip()
        extra = f" — {desc}" if desc else ""
        shown = r["intent"]
        if r.get("intent_override"):
            shown = f"{shown} (fallback from {r['intent_override']})"
        lines.append(f"- {r['path']} — {shown}{extra}")
    parts.append("\n".join(lines))

    fc: list[str] = ["## File contents (from workspace; read / modify only)"]
    has_fc = False
    for r in files:
        intent = (r["intent"] or "").lower()
        if intent not in ("read", "modify"):
            continue
        rel = (r["path"] or "").replace("\\", "/").strip()
        if not rel:
            continue
        body, ok = _file_contents_block(repo_root, rel)
        has_fc = True
        if ok:
            fc.append(f"### {rel}\n```\n{body}\n```")
        else:
            fc.append(f"### {rel}\n_(file not found in workspace — treat as empty / create as needed)_")
    if has_fc:
        parts.append("\n".join(fc))

    comments = conn.execute(
        """
        SELECT author_role, comment_type, body, created_at
        FROM comments
        WHERE work_item_id = ?
        ORDER BY created_at ASC
        """,
        (work_item_id,),
    ).fetchall()
    decs = conn.execute(
        """
        SELECT decision_role, verdict, reason_code, explanation, suggested_fix, created_at
        FROM decisions
        WHERE work_item_id = ?
        ORDER BY created_at ASC
        """,
        (work_item_id,),
    ).fetchall()

    # Highlight the actionable feedback for retries (keep it compact).
    feedback_lines: list[str] = []
    if comments:
        for c in comments:
            ct = (c["comment_type"] or "").strip().lower()
            if ct not in ("rejection", "decision", "instruction"):
                continue
            body = (c["body"] or "").strip()
            if not body:
                continue
            feedback_lines.append(
                f"- [{c['author_role']}/{c['comment_type']}] {_truncate(body, 4000)}"
            )
    if decs:
        for d in decs:
            verdict = (d["verdict"] or "").strip().lower()
            if verdict != "rejected":
                continue
            role = (d["decision_role"] or "").strip()
            rc = (d["reason_code"] or "").strip()
            expl = (d["explanation"] or "").strip()
            sug = (d["suggested_fix"] or "").strip()
            head = f"- role={role} verdict={verdict}"
            if rc:
                head += f" reason_code={rc}"
            feedback_lines.append(head)
            if expl:
                feedback_lines.append(f"  explanation: {_truncate(expl, 2000)}")
            if sug:
                feedback_lines.append(f"  suggested_fix: {_truncate(sug, 2000)}")

    if feedback_lines:
        parts.append("## Previous feedback (MUST address)\n" + "\n".join(feedback_lines))

    if comments:
        cl = ["## Comments (all)"]
        for c in comments:
            body = (c["body"] or "")[:8000]
            cl.append(f"- [{c['author_role']}/{c['comment_type']}] {body}")
        parts.append("\n".join(cl))

    if decs:
        dl = ["## Prior decisions"]
        for d in decs:
            rc = d["reason_code"] or ""
            expl = (d["explanation"] or "")[:4000]
            sug = (d["suggested_fix"] or "")[:2000]
            dl.append(
                f"- role={d['decision_role']} verdict={d['verdict']} reason_code={rc}\n  {expl}"
            )
            if sug.strip():
                dl.append(f"  suggested_fix: {sug}")
        parts.append("\n".join(dl))

    parts.append("## Task")
    parts.append(f"work_item_id: {wi['id']}")
    parts.append(f"title: {wi['title']}")
    parts.append(f"description:\n{wi['description'] or '(none)'}")

    parts.append(FORGE_APPLY_INSTRUCTIONS.strip())

    return "\n\n".join(p for p in parts if p).strip()
