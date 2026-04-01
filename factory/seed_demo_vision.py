"""
Идемпотентный seed: Vision → Epic → 2×Story → 4×Task → атомы в ready_for_work.

Запуск: ``python -m factory.seed_demo_vision``

Если корневой Vision ``demo_vis_mvp_os`` уже есть — выход без изменений.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .composition import wire
from .db import gen_id
from .models import Role

DEMO_VISION = "demo_vis_mvp_os"
DEMO_EPIC = "demo_epic_live_dashboard"
DEMO_STORY_HIER = "demo_story_hierarchy_ui"
DEMO_STORY_LOG = "demo_story_event_log"

DEMO_TASKS = (
    ("demo_task_s1_api", DEMO_STORY_HIER, "API дерева и списка задач", "GET /api/tree, GET /api/tasks"),
    ("demo_task_s1_detail", DEMO_STORY_HIER, "Панель деталей", "GET /api/tasks/<id>"),
    ("demo_task_s2_feed", DEMO_STORY_LOG, "Лента event_log", "GET /api/events"),
    ("demo_task_s2_poll", DEMO_STORY_LOG, "Polling статусов", "refresh каждые 5 с"),
)

# (task_id, title, description, files list of (path, intent))
DEMO_ATOMS = (
    (
        "demo_task_s1_api",
        "Atom: docstring в hello_qwen",
        "Минимальная правка factory/hello_qwen.py для smoke forge",
        (("factory/hello_qwen.py", "modify"),),
    ),
    (
        "demo_task_s1_api",
        "Atom: комментарий в logging",
        "Второй атом задачи — factory/logging.py",
        (("factory/logging.py", "modify"),),
    ),
    (
        "demo_task_s1_detail",
        "Atom: правка config header",
        "Точечная правка factory/config.py",
        (("factory/config.py", "modify"),),
    ),
    (
        "demo_task_s2_feed",
        "Atom: hello_qwen второй прогон",
        "Дублирующий путь для независимого run_id",
        (("factory/hello_qwen.py", "modify"),),
    ),
    (
        "demo_task_s2_feed",
        "Atom: logging второй файл",
        "factory/db.py read-only области — только modify маленький комментарий",
        (("factory/logging.py", "modify"),),
    ),
    (
        "demo_task_s2_poll",
        "Atom: config polling note",
        "Метка в config для проверки журнала",
        (("factory/config.py", "modify"),),
    ),
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%f") + "Z"


def run_seed_demo_vision(db_path: Path | None = None) -> None:
    factory = wire(db_path)
    conn = factory["conn"]
    if conn.execute("SELECT 1 FROM work_items WHERE id = ?", (DEMO_VISION,)).fetchone():
        print(f"seed-demo-vision: уже есть ({DEMO_VISION}). Пропуск.")
        return

    now = _now()

    def ins_wi(
        wi_id: str,
        parent_id: str | None,
        root_id: str,
        kind: str,
        title: str,
        description: str,
        status: str,
        owner: str,
        creator: str,
        depth: int,
        priority: int,
    ) -> None:
        conn.execute(
            """
            INSERT INTO work_items (
                id, parent_id, root_id, kind, title, description, status,
                creator_role, owner_role, planning_depth, priority,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                wi_id,
                parent_id,
                root_id,
                kind,
                title,
                description,
                status,
                creator,
                owner,
                depth,
                priority,
                now,
                now,
            ),
        )

    ins_wi(
        DEMO_VISION,
        None,
        DEMO_VISION,
        "vision",
        "Фабрика: MVP операционной системы разработки",
        "Корневая цель: наблюдаемость и ручной запуск атомов из дашборда.",
        "planned",
        Role.PLANNER.value,
        Role.CREATOR.value,
        0,
        1,
    )
    ins_wi(
        DEMO_EPIC,
        DEMO_VISION,
        DEMO_VISION,
        "epic",
        "Живой дашборд с реальными данными",
        "SQLite → HTTP API → factory-os.html",
        "planned",
        Role.PLANNER.value,
        Role.CREATOR.value,
        1,
        5,
    )
    ins_wi(
        DEMO_STORY_HIER,
        DEMO_EPIC,
        DEMO_VISION,
        "story",
        "Отображение иерархии задач",
        "Дерево Vision → … → Atom из БД",
        "planned",
        Role.PLANNER.value,
        Role.CREATOR.value,
        2,
        10,
    )
    ins_wi(
        DEMO_STORY_LOG,
        DEMO_EPIC,
        DEMO_VISION,
        "story",
        "Журнал событий",
        "event_log в UI с polling",
        "planned",
        Role.PLANNER.value,
        Role.CREATOR.value,
        2,
        11,
    )

    pri = 20
    for tid, spid, ttl, desc in DEMO_TASKS:
        ins_wi(
            tid,
            spid,
            DEMO_VISION,
            "task",
            ttl,
            desc,
            "planned",
            Role.PLANNER.value,
            Role.PLANNER.value,
            3,
            pri,
        )
        pri += 1

    atom_n = 0
    for task_id, title, desc, files in DEMO_ATOMS:
        atom_n += 1
        aid = f"demo_atom_{atom_n:02d}"
        ins_wi(
            aid,
            task_id,
            DEMO_VISION,
            "atom",
            title,
            desc,
            "ready_for_work",
            Role.FORGE.value,
            Role.PLANNER.value,
            4,
            30 + atom_n,
        )
        for path, intent in files:
            conn.execute(
                """
                INSERT INTO work_item_files (id, work_item_id, path, intent, description, required)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (gen_id("wif"), aid, path, intent, "demo seed_demo_vision"),
            )
        conn.execute(
            """
            INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at)
            VALUES (?, 'forge_inbox', ?, ?)
            """,
            (aid, 10 + atom_n, now),
        )

    conn.commit()
    print(f"seed-demo-vision: создано Vision {DEMO_VISION}, Epic, 2 Story, 4 Task, {len(DEMO_ATOMS)} атомов (ready_for_work + forge_inbox).")


def main() -> None:
    from .config import resolve_db_path

    run_seed_demo_vision(resolve_db_path())


if __name__ == "__main__":
    main()
