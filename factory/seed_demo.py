"""
Идемпотентное наполнение ``factory.db`` демо для живого дашборда.

Каноническая лестница: Vision → Epic → Story → Task → Atom (``WorkItemOps``).

Повторный запуск: если уже есть Vision с ``metadata.seed = SEED_MARKER`` — выход без изменений.

Часть атомов остаётся в ``draft``; часть переводится в ``ready_for_work`` + ``forge_inbox`` через
``forge_next_atom.mark_atom_ready_for_forge`` (судья + очередь, как в runtime).

Raw SQL: ``architect_comments``; ``UPDATE metadata`` у Vision.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .composition import wire
from .db import gen_id
from .forge_next_atom import mark_atom_ready_for_forge
from .fsm import StateMachine
from .models import EventType, Role, Severity
from .work_items import WorkItemOps

SEED_MARKER = "factory_os_live_v1"

VISION_TITLE = "Factory OS - операционная система фабрики"
VISION_DESC = (
    "Живой дашборд: SQLite → read-only API → factory-os.html. "
    f"Демо-seed ({SEED_MARKER})."
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%f") + "Z"


def _seed_already_applied(conn) -> bool:
    r = conn.execute(
        """
        SELECT 1 FROM work_items
        WHERE kind = 'vision'
          AND json_extract(COALESCE(metadata, '{}'), '$.seed') = ?
        LIMIT 1
        """,
        (SEED_MARKER,),
    ).fetchone()
    return r is not None


def _child(
    ops: WorkItemOps,
    parent_id: str,
    kind: str,
    title: str,
    description: str | None,
    *,
    files: list[dict] | None = None,
) -> str:
    return ops.create_child(
        parent_id,
        kind,
        title,
        description,
        creator_role=Role.CREATOR.value,
        files=files,
        auto_commit=False,
    )


def _atom_file(path: str, intent: str = "modify") -> list[dict]:
    return [{"path": path, "intent": intent, "description": None}]


def run_seed_demo(db_path: Path | None = None) -> None:
    factory = wire(db_path)
    conn = factory["conn"]
    ops: WorkItemOps = factory["ops"]
    sm: StateMachine = factory["sm"]
    orch = factory["orchestrator"]
    logger = factory["logger"]

    if _seed_already_applied(conn):
        print(f"seed-demo: already applied ({SEED_MARKER}). Skip.")
        return

    now = _now()
    atom_ids: list[str] = []
    n_ready = 4

    try:
        vision_id = ops.create_vision(VISION_TITLE, VISION_DESC, auto_commit=False)

        epic = _child(
            ops,
            vision_id,
            "epic",
            "Живой дашборд",
            "HTTP API (read-only) + factory-os.html, обновление статусов и журнала.",
        )

        st_api = _child(
            ops,
            epic,
            "story",
            "API-сервер для дашборда",
            "GET /api/work-items, /api/runs, /api/events, /api/stats; SQLite mode=ro.",
        )
        t_api = _child(
            ops,
            st_api,
            "task",
            "Реализовать read-only эндпоинты",
            "Порт по умолчанию 8420: python -m factory --dashboard",
        )
        atom_ids.append(
            _child(
                ops,
                t_api,
                "atom",
                "Создать GET /api/work-items (фильтры kind, status)",
                "Список задач с вложенными work_item_files.",
                files=_atom_file("factory/dashboard_api_read.py"),
            )
        )
        atom_ids.append(
            _child(
                ops,
                t_api,
                "atom",
                "Создать GET /api/runs и GET /api/runs/<id>",
                "Прогоны с run_steps и file_changes; disambiguation run_id vs work_item_id.",
                files=_atom_file("factory/dashboard_api.py"),
            )
        )
        atom_ids.append(
            _child(
                ops,
                t_api,
                "atom",
                "Создать GET /api/work-items/<id>/tree",
                "Иерархия потомков от любого узла.",
                files=_atom_file("factory/dashboard_live_read.py"),
            )
        )
        atom_ids.append(
            _child(
                ops,
                t_api,
                "atom",
                "Сводка GET /api/stats (статусы, последние события)",
                "KPI-блок на дашборде.",
                files=_atom_file("factory/dashboard_live_read.py"),
            )
        )

        st_ui = _child(
            ops,
            epic,
            "story",
            "Подключение UI к API",
            "factory-os.html: fetch, автообновление, empty states.",
        )
        t_ui = _child(
            ops,
            st_ui,
            "task",
            "Заменить моки на данные из БД",
            "Вкладки Задачи / Кузница / Журнал.",
        )
        atom_ids.append(
            _child(
                ops,
                t_ui,
                "atom",
                "Загрузка дерева Vision из /api/visions/.../tree",
                "Текущий UI уже использует снимок API.",
                files=_atom_file("factory-os.html"),
            )
        )
        atom_ids.append(
            _child(
                ops,
                t_ui,
                "atom",
                "KPI и журнал: polling /api/stats и /api/events",
                "Интервал 5 с (FACTORY_POLL_MS).",
                files=_atom_file("factory-os.html"),
            )
        )
        atom_ids.append(
            _child(
                ops,
                t_ui,
                "atom",
                "Сообщение «API сервер не запущен» при offline",
                "resolveApiBase + connection banner.",
                files=_atom_file("factory-os.html"),
            )
        )

        arch_comments: list[tuple[str, str]] = [
            (
                vision_id,
                "Архитектор: единый factory.db для оркестратора и дашборда (read-only HTTP).",
            ),
            (
                epic,
                "Архитектор: дашборд — наблюдаемость без мутаций через HTTP.",
            ),
            (
                st_api,
                "Архитектор: контракт REST под factory-os.html и будущие клиенты.",
            ),
        ]
        for wi_id, body in arch_comments:
            conn.execute(
                """
                INSERT INTO architect_comments (id, work_item_id, comment, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (gen_id("ac"), wi_id, body, now),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    try:
        for aid in atom_ids[:n_ready]:
            mark_atom_ready_for_forge(conn, sm, aid, orchestrator=orch)
        conn.execute(
            "UPDATE work_items SET metadata = ? WHERE id = ?",
            (
                json.dumps(
                    {
                        "seed": SEED_MARKER,
                        "forge_ready_partial": n_ready,
                        "atoms_total": len(atom_ids),
                    }
                ),
                vision_id,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    logger.log(
        EventType.TASK_STATUS_CHANGED,
        "system",
        "seed_demo",
        "seed-demo Factory OS: draft + mark_atom_ready_for_forge (часть атомов)",
        severity=Severity.INFO,
        payload={
            "vision": vision_id,
            "seed": SEED_MARKER,
            "atoms": atom_ids,
            "ready_for_forge": atom_ids[:n_ready],
        },
        tags=["seed", "demo"],
    )
    conn.commit()

    print(
        "seed-demo: OK - "
        f"{VISION_TITLE} -> 1 Epic -> 2 Story -> 7 atoms "
        f"({n_ready} ready_for_work+forge_inbox, {len(atom_ids) - n_ready} draft); "
        f"marker={SEED_MARKER}"
    )


if __name__ == "__main__":
    run_seed_demo()
