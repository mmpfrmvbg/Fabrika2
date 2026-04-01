"""Компактное дерево файлов workspace для промпта Planner (без полного чтения файлов)."""

from __future__ import annotations

import ast
import os
from pathlib import Path

_DEFAULT_IGNORE_DIRS = frozenset(
    {
        "__pycache__",
        ".git",
        "node_modules",
        "venv",
        ".venv",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
    }
)
_READ_CAP = 128 * 1024
# AST — только для первых N .py, чтобы уложиться в бюджет времени на больших деревьях.
_MAX_PY_AST = 50


def _should_skip_dir(name: str, ignore: frozenset[str]) -> bool:
    return name in ignore


def _should_skip_file(name: str) -> bool:
    if name.endswith(".pyc") or name.endswith(".db") or name.endswith(".lock"):
        return True
    if name.endswith((".html", ".htm")):
        return True
    if name.endswith("-shm") or name.endswith("-wal") or name.endswith("-journal"):
        return True
    if name == ".env":
        return True
    return False


def _line_count(path: Path) -> int:
    try:
        n = 0
        with path.open("rb") as f:
            while True:
                chunk = f.read(256 * 1024)
                if not chunk:
                    break
                n += chunk.count(b"\n")
        return n
    except OSError:
        return 0


def _line_count_from_bytes(raw: bytes) -> int:
    if not raw:
        return 0
    n = raw.count(b"\n")
    if not raw.endswith(b"\n"):
        n += 1
    return n


def _py_summary_from_source(src: str, path: Path) -> str:
    try:
        tree = ast.parse(src, filename=str(path))
    except (SyntaxError, ValueError):
        return ""

    bits: list[str] = []
    doc = ast.get_docstring(tree)
    if doc:
        first = doc.strip().split("\n", 1)[0].strip()
        if len(first) > 100:
            first = first[:97] + "..."
        bits.append(first)

    tops: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = [
                n.name
                for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ][:6]
            if methods:
                tops.append(f"class {node.name}: {', '.join(methods)}")
            else:
                tops.append(f"class {node.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            tops.append(f"def {node.name}")

    if tops:
        tail = ", ".join(tops[:4])
        if len(tops) > 4:
            tail += "..."
        bits.append(tail)

    return " — ".join(bits) if bits else ""


def _file_line(path: Path, is_py: bool, ast_remaining: list[int]) -> str:
    if is_py:
        try:
            sz = path.stat().st_size
        except OSError:
            return "(0 lines)"
        if sz > _READ_CAP:
            n = _line_count(path)
            return f"({n} lines)"
        try:
            raw = path.read_bytes()
        except OSError:
            return "(0 lines)"
        n = _line_count_from_bytes(raw)
        summ = ""
        if ast_remaining[0] > 0:
            ast_remaining[0] -= 1
            try:
                src = raw.decode("utf-8", errors="replace")
                summ = _py_summary_from_source(src, path)
            except (SyntaxError, ValueError):
                summ = ""
        core = f"({n} lines)"
        return f"{core} — {summ}" if summ else core
    try:
        sz = path.stat().st_size
    except OSError:
        sz = 0
    return f"({sz} bytes)"


def _insert(tree: dict, parts: list[str], leaf_suffix: str) -> None:
    if len(parts) == 1:
        tree[parts[0]] = ("__leaf__", leaf_suffix)
        return
    head, *rest = parts
    nxt = tree.setdefault(head, {})
    if not isinstance(nxt, dict):
        return
    _insert(nxt, rest, leaf_suffix)


def _walk(node: dict, indent: str, out: list[str]) -> None:
    dirs = sorted((k, v) for k, v in node.items() if isinstance(v, dict))
    leaves = sorted((k, v) for k, v in node.items() if not isinstance(v, dict))
    for name, ch in dirs:
        out.append(f"{indent}{name}/")
        _walk(ch, indent + "  ", out)
    for name, val in leaves:
        if isinstance(val, tuple) and val[0] == "__leaf__":
            out.append(f"{indent}{name} {val[1]}")
        else:
            out.append(f"{indent}{name}")


def scan_workspace(
    root: Path | str,
    max_files: int = 200,
    ignore: list[str] | None = None,
) -> str:
    """
    Возвращает компактное текстовое представление файловой структуры workspace.
    """
    root_path = Path(root).resolve()
    ign = frozenset(ignore) if ignore else _DEFAULT_IGNORE_DIRS

    if not root_path.is_dir():
        return f"(not a directory: {root_path})"

    rel_paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root_path, topdown=True):
        dirnames[:] = sorted(d for d in dirnames if not _should_skip_dir(d, ign))
        for fn in sorted(filenames):
            if _should_skip_file(fn):
                continue
            full = Path(dirpath) / fn
            try:
                rel = full.relative_to(root_path)
            except ValueError:
                continue
            rel_paths.append(str(rel).replace("\\", "/"))

    rel_paths.sort()
    truncated = 0
    if len(rel_paths) > max_files:
        truncated = len(rel_paths) - max_files
        rel_paths = rel_paths[:max_files]
    ast_remaining = [_MAX_PY_AST]
    tree: dict = {}
    for rel in rel_paths:
        parts = rel.split("/")
        full = root_path.joinpath(*parts)
        is_py = full.suffix == ".py"
        suffix = _file_line(full, is_py, ast_remaining)
        _insert(tree, parts, suffix)

    out: list[str] = [f"Current workspace structure (root: {root_path}):"]
    if not rel_paths:
        out.append("  (no files)")
    else:
        _walk(tree, "  ", out)
    if truncated:
        out.append(f"... and {truncated} more")
    return "\n".join(out)
