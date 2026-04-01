"""
Первый «реальный» Vision в БД для дашборда.

Запуск из каталога ``proekt/``:
  python -m factory.seed_real_vision

БД: ``FACTORY_DB`` / по умолчанию ``factory.db``.
"""

from __future__ import annotations

from .composition import wire
from .config import resolve_db_path
from .db import gen_id
from .models import QueueName, Role, WorkItemStatus


def run_seed() -> tuple[str, str, str, str, str, str, str]:
    db = resolve_db_path()
    f = wire(db)
    ops = f["ops"]
    conn = f["conn"]

    vid = ops.create_vision(
        "Fabrika v2.0 — автономная система разработки",
        "Демо-дерево для живого дашборда (SQLite + API).",
        auto_commit=False,
    )
    eid = ops.create_child(
        vid,
        "epic",
        "Живой дашборд с реальными данными",
        auto_commit=False,
    )
    sid = ops.create_child(
        eid,
        "story",
        "API-сервер для чтения SQLite",
        auto_commit=False,
    )
    a1 = ops.create_child(
        sid,
        "atom",
        "Создать factory/api_server.py с эндпоинтами для work_items и events",
        auto_commit=False,
    )
    a2 = ops.create_child(
        sid,
        "atom",
        "Подключить factory-os.html к API через fetch",
        auto_commit=False,
    )
    a3 = ops.create_child(
        sid,
        "atom",
        "Добавить auto-refresh и фильтрацию в дашборде",
        auto_commit=False,
    )
    a4 = ops.create_child(
        sid,
        "atom",
        "Демо: кнопка «Запустить Forge» (ready_for_work + очередь)",
        auto_commit=False,
    )
    conn.execute(
        "UPDATE work_items SET status = ?, owner_role = ? WHERE id = ?",
        (WorkItemStatus.READY_FOR_WORK.value, Role.FORGE.value, a4),
    )
    conn.execute(
        """
        INSERT INTO work_item_files (id, work_item_id, path, intent, required)
        VALUES (?, ?, ?, 'modify', 1)
        """,
        (gen_id("wif"), a4, "factory/__init__.py"),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO work_item_queue
            (work_item_id, queue_name, priority, available_at, attempts)
        VALUES (?, ?, 10, datetime('now'), 0)
        """,
        (a4, QueueName.FORGE_INBOX.value),
    )

    conn.execute(
        "UPDATE work_items SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (WorkItemStatus.DONE.value, a1),
    )
    conn.execute(
        "UPDATE work_items SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (WorkItemStatus.IN_PROGRESS.value, a2),
    )
    # «open» в смысле ещё не взято в работу — в FSM ближе всего draft
    conn.execute(
        "UPDATE work_items SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (WorkItemStatus.DRAFT.value, a3),
    )

    conn.commit()
    return vid, eid, sid, a1, a2, a3, a4


def main() -> None:
    vid, eid, sid, a1, a2, a3, a4 = run_seed()
    print("Seed OK")
    print("  vision:", vid)
    print("  epic:  ", eid)
    print("  story: ", sid)
    print("  atom-1 (done):        ", a1)
    print("  atom-2 (in_progress): ", a2)
    print("  atom-3 (draft/open):  ", a3)
    print("  atom-4 (ready_for_work, demo run): ", a4)


if __name__ == "__main__":
    main()
