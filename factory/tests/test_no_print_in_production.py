from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_no_print_calls_in_factory_production_code() -> None:
    offenders: list[str] = []
    for path in ROOT.glob("**/*.py"):
        rel = path.relative_to(ROOT)
        if rel.parts[0] == "tests":
            continue
        text = path.read_text(encoding="utf-8")
        if "print(" in text:
            offenders.append(str(rel))

    assert not offenders, f"Found print() in production files: {offenders}"
