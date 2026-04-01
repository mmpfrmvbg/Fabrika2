"""Planner agent: декомпозиция work_item до атомов (DRY_RUN или Qwen CLI).

Оркестратор вызывает :func:`run_planner` по очереди planner_inbox.
HTTP API (`POST /api/visions`) также использует planner синхронно для MVP.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ..config import AccountManager
from ..contracts.planner import PlannerInput, PlannerOutput, PlannerOutputItem
from ..db import gen_id
from ..models import EventType, QueueName, Role, RunType, StepKind, WorkItemStatus
from ..planner import extract_json_object
from ..qwen_cli_runner import run_qwen_cli
from ..forge_sandbox import workspace_root
from ..work_items import WorkItemOps
from ..workspace_scanner import scan_workspace
from ._helpers import finish_run, insert_run, insert_run_step, lease_queue_row

if TYPE_CHECKING:
    from ..orchestrator_core import Orchestrator


def _read_prompt_template() -> str:
    p = Path(__file__).resolve().parent.parent / "prompts" / "planner_prompt_v1.txt"
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


_PLANNER_PROMPT_V1 = _read_prompt_template()


def build_planner_prompt(inp: PlannerInput) -> str:
    tpl = _PLANNER_PROMPT_V1 or ""
    ws = scan_workspace(workspace_root(), max_files=100)
    return tpl.format(
        kind=inp.kind,
        title=inp.title,
        description=(inp.description or "").strip() or "(none)",
        workspace_structure=ws,
    ).strip()


def _env_qwen_dry_run() -> bool:
    raw = os.environ.get("FACTORY_QWEN_DRY_RUN")
    if raw is None:
        return True
    s = raw.strip().lower()
    return s not in ("0", "false", "no", "off")


def _dry_run_output(inp: PlannerInput) -> PlannerOutput:
    """Детерминированная декомпозиция для тестов/демо при FACTORY_QWEN_DRY_RUN=1."""
    t = (inp.title or "").strip()
    d = (inp.description or "").strip().lower()
    is_auth = any(x in d for x in ("jwt", "auth", "авториза", "регистра", "password", "сброс"))

    is_calc = ("калькулятор" in d or "calculator" in d) and (
        "power" in d or "метод" in d or "sqrt" in d
    )
    if is_calc:
        epic = PlannerOutputItem(
            kind="epic",
            title="Калькулятор",
            description="Epic: доработка существующего модуля calculator.",
            children=[
                PlannerOutputItem(
                    kind="story",
                    title="Новые операции Calculator",
                    description="Story: методы калькулятора по описанию vision.",
                    children=[
                        PlannerOutputItem(
                            kind="atom",
                            title="Добавить метод power в Calculator",
                            description=(
                                "Acceptance criteria:\n"
                                "- Метод power реализован в calculator/calc.py\n"
                                "- Согласован с классом Calculator и существующими тестами"
                            ),
                            files=["calculator/calc.py"],
                        ),
                    ],
                ),
            ],
        )
        return PlannerOutput(
            items=[epic],
            reasoning="DRY_RUN: калькулятор + power/sqrt по ключевым словам (пути из workspace).",
        )

    if is_auth:
        epic = PlannerOutputItem(
            kind="epic",
            title="Система авторизации",
            description="Epic: пользовательская аутентификация и управление учётными данными.",
            children=[
                PlannerOutputItem(
                    kind="story",
                    title="Регистрация пользователя",
                    description="Story: сценарий создания аккаунта.",
                    children=[
                        PlannerOutputItem(
                            kind="atom",
                            title="Реализовать эндпоинт POST /register",
                            description="Acceptance criteria:\n- Есть маршрут POST /register\n- Возвращает 201 и id пользователя\n- Ошибки валидации возвращают 400 с понятным сообщением",
                            files=["src/auth/register.py"],
                        ),
                        PlannerOutputItem(
                            kind="atom",
                            title="Добавить валидацию email и пароля",
                            description="Acceptance criteria:\n- Email валиден и уникален\n- Пароль соответствует минимальным требованиям\n- Сообщения об ошибках не раскрывают лишних деталей",
                            files=["src/auth/validation.py"],
                        ),
                    ],
                ),
                PlannerOutputItem(
                    kind="story",
                    title="JWT-аутентификация",
                    description="Story: логин и проверка токенов.",
                    children=[
                        PlannerOutputItem(
                            kind="atom",
                            title="Реализовать генерацию и верификацию JWT",
                            description="Acceptance criteria:\n- Генерация access token\n- Проверка подписи и срока действия\n- Единая функция decode/verify используется в middleware",
                            files=["src/auth/jwt.py"],
                        )
                    ],
                ),
                PlannerOutputItem(
                    kind="story",
                    title="Сброс пароля",
                    description="Story: запрос и подтверждение сброса пароля.",
                    children=[
                        PlannerOutputItem(
                            kind="atom",
                            title="Реализовать эндпоинт POST /reset-password",
                            description="Acceptance criteria:\n- Есть маршрут POST /reset-password\n- Не раскрывает существование email\n- Создаёт одноразовый токен сброса",
                            files=["src/auth/reset_password.py"],
                        )
                    ],
                ),
            ],
        )
        return PlannerOutput(items=[epic], reasoning="DRY_RUN: auth/JWT шаблон по ключевым словам.")

    # generic: 2 epics → 2 stories → 2 atoms (>=2 epic/story/atom)
    base = t or "Новая фича"
    ep1 = PlannerOutputItem(
        kind="epic",
        title=f"{base}: API",
        description="Epic: публичные API и контракт.",
        children=[
            PlannerOutputItem(
                kind="story",
                title="Основные эндпоинты",
                description="Story: CRUD/операции первого уровня.",
                children=[
                    PlannerOutputItem(
                        kind="atom",
                        title="Добавить первый эндпоинт",
                        description="Acceptance criteria:\n- Эндпоинт добавлен\n- Возвращает валидный JSON\n- Есть базовая обработка ошибок",
                        files=["src/api/endpoints.py"],
                    ),
                    PlannerOutputItem(
                        kind="atom",
                        title="Добавить входную валидацию",
                        description="Acceptance criteria:\n- Валидация входных полей\n- 400 при невалидном запросе\n- Сообщения об ошибках стабильны",
                        files=["src/api/schemas.py"],
                    ),
                ],
            )
        ],
    )
    ep2 = PlannerOutputItem(
        kind="epic",
        title=f"{base}: UI",
        description="Epic: базовые экраны и интеграция с API.",
        children=[
            PlannerOutputItem(
                kind="story",
                title="Первый экран",
                description="Story: минимальный UI для сценария.",
                children=[
                    PlannerOutputItem(
                        kind="atom",
                        title="Добавить компонент UI",
                        description="Acceptance criteria:\n- Компонент отображает состояние загрузки\n- Ошибки отображаются пользователю\n- Данные приходят из API",
                        files=["src/ui/App.tsx"],
                    )
                ],
            ),
            PlannerOutputItem(
                kind="story",
                title="Связка UI ↔ API",
                description="Story: клиент для запросов.",
                children=[
                    PlannerOutputItem(
                        kind="atom",
                        title="Добавить API-клиент",
                        description="Acceptance criteria:\n- Единая функция fetch\n- Таймаут/ошибка обрабатываются\n- Типы ответа описаны",
                        files=["src/ui/api.ts"],
                    )
                ],
            ),
        ],
    )
    return PlannerOutput(items=[ep1, ep2], reasoning="DRY_RUN: generic 2×2×atoms.")


def _collect_tree_stats(items: list[PlannerOutputItem]) -> dict[str, int]:
    c = {"epics": 0, "stories": 0, "tasks": 0, "atoms": 0}

    def walk(it: PlannerOutputItem) -> None:
        if it.kind == "epic":
            c["epics"] += 1
        elif it.kind == "story":
            c["stories"] += 1
        elif it.kind == "task":
            c["tasks"] += 1
        elif it.kind == "atom":
            c["atoms"] += 1
        for ch in it.children:
            walk(ch)

    for x in items:
        walk(x)
    return c


def _persist_items(
    *,
    conn,
    logger,
    parent_id: str,
    items: list[PlannerOutputItem],
) -> dict[str, int]:
    ops = WorkItemOps(conn, logger)
    stats = {"epics": 0, "stories": 0, "tasks": 0, "atoms": 0}

    def add(parent: str, it: PlannerOutputItem) -> None:
        nonlocal stats
        kind = it.kind
        desc = (it.description or "").strip() or None
        child_id = ops.create_child(
            parent,
            kind,
            it.title.strip(),
            desc,
            creator_role=Role.PLANNER.value,
            files=[{"path": p, "intent": "modify"} for p in (it.files or [])] if kind == "atom" else None,
            auto_commit=False,
        )
        if kind == "epic":
            stats["epics"] += 1
            conn.execute(
                "UPDATE work_items SET status = ?, owner_role = ? WHERE id = ?",
                (WorkItemStatus.PLANNED.value, Role.PLANNER.value, child_id),
            )
        elif kind == "story":
            stats["stories"] += 1
            conn.execute(
                "UPDATE work_items SET status = ?, owner_role = ? WHERE id = ?",
                (WorkItemStatus.PLANNED.value, Role.PLANNER.value, child_id),
            )
        elif kind == "task":
            stats["tasks"] += 1
            conn.execute(
                "UPDATE work_items SET status = ?, owner_role = ? WHERE id = ?",
                (WorkItemStatus.PLANNED.value, Role.PLANNER.value, child_id),
            )
        elif kind == "atom":
            stats["atoms"] += 1
            # готов к кузнице сразу
            conn.execute(
                "UPDATE work_items SET status = ?, owner_role = ? WHERE id = ?",
                (WorkItemStatus.READY_FOR_WORK.value, Role.FORGE.value, child_id),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO work_item_queue
                    (work_item_id, queue_name, priority, available_at, attempts)
                VALUES (?, ?, 10, datetime('now'), 0)
                """,
                (child_id, QueueName.FORGE_INBOX.value),
            )
        for ch in it.children:
            add(child_id, ch)

    for it in items:
        add(parent_id, it)
    return stats


def decompose_with_planner(
    *,
    conn,
    logger,
    inp: PlannerInput,
) -> PlannerOutput:
    """
    Выполняет декомпозицию inp в PlannerOutput (DRY_RUN или Qwen CLI) и пишет дерево в SQLite.
    Возвращает PlannerOutput (reasoning сохраняется для API).
    """
    if _env_qwen_dry_run():
        out = _dry_run_output(inp)
    else:
        prompt = build_planner_prompt(inp)
        am = AccountManager(conn, logger)
        fr = run_qwen_cli(
            conn=conn,
            account_manager=am,
            logger=logger,
            work_item_id=inp.work_item_id,
            run_id=None,
            title="planner_decompose",
            description="",
            full_prompt=prompt,
            cwd=str(Path.cwd()),
        )
        combined = f"{fr.stdout or ''}\n{fr.stderr or ''}"
        data = extract_json_object(combined) if fr.ok else None
        if not fr.ok or data is None:
            # retry once with stricter hint
            prompt2 = prompt + "\n\nВерни ТОЛЬКО JSON-объект. Без Markdown. Без текста вокруг."
            fr2 = run_qwen_cli(
                conn=conn,
                account_manager=am,
                logger=logger,
                work_item_id=inp.work_item_id,
                run_id=None,
                title="planner_decompose_retry",
                description="",
                full_prompt=prompt2,
                cwd=str(Path.cwd()),
            )
            combined2 = f"{fr2.stdout or ''}\n{fr2.stderr or ''}"
            data = extract_json_object(combined2) if fr2.ok else None
            if not fr2.ok or data is None:
                raise ValueError("planner LLM: ответ не JSON (проверьте FACTORY_QWEN_DRY_RUN и ключи)")
        try:
            out = PlannerOutput.model_validate(data)
        except ValidationError as e:
            raise ValueError(f"planner LLM: JSON не соответствует контракту PlannerOutput: {e}") from e

    stats = _persist_items(conn=conn, logger=logger, parent_id=inp.work_item_id, items=out.items)
    logger.log(
        EventType.PLANNER_DECOMPOSED,
        "work_item",
        inp.work_item_id,
        "Planner decomposed work item",
        work_item_id=inp.work_item_id,
        actor_role=Role.PLANNER.value,
        payload={"tree_stats": stats, "reasoning": out.reasoning, "schema": "planner_output_v1"},
        tags=["planner", "decompose"],
    )
    return out


def run_planner(orchestrator: Orchestrator, item: dict) -> None:
    conn = orchestrator.conn
    sm = orchestrator.sm
    logger = orchestrator.logger
    accounts = orchestrator.accounts

    wi_id = item["work_item_id"]
    wi = conn.execute("SELECT id, kind, title, description, planning_depth FROM work_items WHERE id = ?", (wi_id,)).fetchone()
    if not wi:
        return
    account = accounts.get_active_account()
    run_id = gen_id("run")
    lease_queue_row(conn, wi_id, Role.PLANNER)
    insert_run(
        conn,
        run_id=run_id,
        wi_id=wi_id,
        role=Role.PLANNER,
        run_type=RunType.PLAN,
        account_id=account["account_id"],
    )

    logger.log(
        EventType.RUN_STARTED,
        "run",
        run_id,
        "Run started (planner)",
        work_item_id=wi_id,
        run_id=run_id,
        actor_role=Role.PLANNER.value,
        account_id=account["account_id"],
        tags=["planner", "phase2"],
    )

    insert_run_step(
        conn,
        run_id,
        1,
        StepKind.PROMPT.value,
        {
            "step_kind": "prompt",
            "role": "planner",
            "description": "Planner decompose (contracts/planner.py)",
            "input_summary": {"work_item_id": wi_id, "kind": wi["kind"], "title": wi["title"]},
        },
    )
    ok = True
    msg = ""
    try:
        inp = PlannerInput(
            work_item_id=wi_id,
            title=wi["title"] or "",
            description=wi["description"] or "",
            kind=(wi["kind"] or "vision"),
            current_depth=int(wi["planning_depth"] or 0),
            max_depth=4,
        )
        out = decompose_with_planner(conn=conn, logger=logger, inp=inp)
        insert_run_step(
            conn,
            run_id,
            2,
            StepKind.DECISION.value,
            {"step_kind": "decision", "role": "planner", "tree_stats": _collect_tree_stats(out.items)},
            summary="planner_decomposed",
        )
        ok, msg = sm.apply_transition(
            wi_id,
            "planner_decomposed",
            actor_role=Role.PLANNER.value,
            run_id=run_id,
        )
        if ok:
            for row in conn.execute("SELECT id FROM work_items WHERE parent_id = ?", (wi_id,)):
                sm.actions.action_notify_architect(row["id"])
            conn.execute("DELETE FROM work_item_queue WHERE work_item_id = ?", (wi_id,))
        else:
            conn.execute(
                "UPDATE work_item_queue SET lease_owner = NULL, lease_until = NULL WHERE work_item_id = ?",
                (wi_id,),
            )
    except Exception as e:  # noqa: BLE001
        ok = False
        msg = str(e)
        insert_run_step(
            conn,
            run_id,
            99,
            StepKind.ERROR.value,
            {"step_kind": "error", "role": "planner", "error": msg},
            summary="planner_failed",
        )
    finish_run(conn, run_id, ok=ok, error_summary=None if ok else msg, logger=logger)
