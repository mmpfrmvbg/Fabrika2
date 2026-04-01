"""
Один «настоящий» ручной E2E: tiny vision -> epic (stub) -> atom с work_item_files ->
оркестратор -> judge -> forge-worker (``qwen_cli_runner`` + dry по умолчанию) -> review -> done.

Три сценария в CI (разные риски):

- ``--e2e-manual`` — базовый happy-path фабрики (без отдельных assert'ов на контракт раннера).
- ``--e2e-qwen-dry`` — тот же маршрут + явно: вызов раннера, ``ForgeResult.ok`` в артефактах,
  forge ``run.completed``, события forge/Qwen до ``done``.
- ``--e2e-qwen-wet-edit`` — один атом ``factory/hello_qwen.py``, только ``FACTORY_QWEN_DRY_RUN=0``;
  жёсткая проверка ``file_changes`` / ``file_write``.
- ``--e2e-planner`` — Vision → Qwen → JSON → ``work_items`` + ``work_item_files`` (только ``FACTORY_QWEN_DRY_RUN=0``);
  проверка цепочки parent_id и файлов у атомов, без оценки «качества» декомпозиции.
- ``--e2e-planner-forge`` — planner → атом с ``hello_qwen.py`` → ``mark_atom_ready_for_forge`` → ``execute_run_next_atom``
  (тот же FSM/forge, что wet-edit); только ``FACTORY_QWEN_DRY_RUN=0``.
- ``--e2e-qwen-wet-failover`` — тот же wet-edit, но первый вызов CLI симулирует rate limit; нужны ≥2 аккаунта.
- ``--e2e-qwen-wet-forge-no-artifact`` — wet без изменения modify-файлов → ``forge_failed``, ``run.failed.forge_no_artifact``.
- ``--e2e-two-atoms`` — смешанный эпик + ветка с отказом ревью.

Forge-worker пишет ``run_steps`` (summary=qwen_cli_runner), ``file_changes`` по объявленным файлам.
Реальный Qwen CLI: ``FACTORY_QWEN_DRY_RUN=0`` (по умолчанию в раннере — dry-run, без subprocess).

Автопроверка: `assert_trace_integrity()` в конце прогона — без ручного sqlite.

E2E №2: БД ``factory_e2e_two_atoms.db``. E2E №3: временная БД, флаг ``--e2e-forge-qwen-dry`` = алиас к ``--e2e-qwen-dry``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

from . import config as factory_config
from .config import resolve_db_path
from .composition import wire
from .forge_next_atom import execute_run_next_atom, mark_atom_ready_for_forge
from .e2e_qwen_wet_shared import (
    WET_EDIT_ATOM_DESCRIPTION,
    WET_EDIT_HELLO_PATH,
    drive_wet_hello_atom_to_ready_for_work,
)
from .models import CommentType, EventType, Role, WorkItemKind, WorkItemStatus
from .planner import Planner, render_vision_tree
from .qwen_cli_runner import _env_qwen_dry_run, reset_e2e_qwen_simulation_hooks
from .work_items import WorkItemOps


DEFAULT_MANUAL_DB = Path(__file__).resolve().parent.parent / "factory_e2e_manual.db"
DEFAULT_TWO_ATOMS_DB = Path(__file__).resolve().parent.parent / "factory_e2e_two_atoms.db"


def _unlink_quiet(p: Path) -> None:
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass


def _require_event_count(
    conn,
    *,
    work_item_id: str,
    event_type: EventType,
    min_count: int = 1,
) -> None:
    """События с привязкой к work_item (колонка work_item_id)."""
    n = conn.execute(
        """
        SELECT COUNT(*) AS c FROM event_log
        WHERE work_item_id = ? AND event_type = ?
        """,
        (work_item_id, event_type.value),
    ).fetchone()["c"]
    if n < min_count:
        raise AssertionError(
            f"event_log: ожидалось >= {min_count} × {event_type.value} "
            f"для work_item_id={work_item_id}, получено {n}"
        )


def _assert_run_started_completed(
    conn,
    run_id: str,
    *,
    require_completed: bool = True,
) -> None:
    s = conn.execute(
        """
        SELECT COUNT(*) AS c FROM event_log
        WHERE entity_id = ? AND event_type = ?
        """,
        (run_id, EventType.RUN_STARTED.value),
    ).fetchone()["c"]
    if s < 1:
        raise AssertionError(
            f"run {run_id}: ожидался event_type=run.started, got {s}"
        )
    if not require_completed:
        return
    c = conn.execute(
        """
        SELECT COUNT(*) AS c FROM event_log
        WHERE entity_id = ? AND event_type = ?
        """,
        (run_id, EventType.RUN_COMPLETED.value),
    ).fetchone()["c"]
    if c < 1:
        raise AssertionError(
            f"run {run_id}: ожидался event_type=run.completed, got {c}"
        )


def assert_happy_atom(conn, atom_id: str, *, check_events: bool = True) -> None:
    """
    Happy-ветка: атом в done, полный трейс, без blocking failed review_checks.
    """
    st = conn.execute(
        "SELECT status FROM work_items WHERE id = ?",
        (atom_id,),
    ).fetchone()
    if not st or st["status"] != WorkItemStatus.DONE.value:
        raise AssertionError(f"atom должен быть done, получено {st and st['status']}")

    ev = conn.execute(
        """
        SELECT COUNT(*) AS c FROM event_log
        WHERE work_item_id = ? OR entity_id = ?
        """,
        (atom_id, atom_id),
    ).fetchone()["c"]
    if ev < 5:
        raise AssertionError(f"event_log: ожидалось >=5 строк по атому, получено {ev}")

    runs = conn.execute(
        "SELECT id, role FROM runs WHERE work_item_id = ? ORDER BY started_at",
        (atom_id,),
    ).fetchall()
    roles = {r["role"] for r in runs}
    for need in ("judge", "forge", "reviewer"):
        if need not in roles:
            raise AssertionError(f"runs: нет role={need}, есть {roles}")

    run_ids = [x["id"] for x in runs]
    ph = ",".join("?" * len(run_ids))
    rs = conn.execute(
        f"SELECT COUNT(*) AS c FROM run_steps WHERE run_id IN ({ph})",
        run_ids,
    ).fetchone()["c"]
    if rs < 1:
        raise AssertionError(f"run_steps: ожидалось >=1, получено {rs}")

    rc = conn.execute(
        f"SELECT COUNT(*) AS c FROM review_checks WHERE run_id IN ({ph})",
        run_ids,
    ).fetchone()["c"]
    if rc < 1:
        raise AssertionError(f"review_checks: ожидалось >=1, получено {rc}")

    blocking_failed = conn.execute(
        f"""
        SELECT COUNT(*) AS c FROM review_checks rc
        WHERE rc.run_id IN ({ph}) AND rc.status = 'failed' AND rc.is_blocking = 1
        """,
        run_ids,
    ).fetchone()["c"]
    if blocking_failed > 0:
        raise AssertionError(f"happy-атом: не ожидаются blocking failed review_checks, найдено {blocking_failed}")

    passed_any = conn.execute(
        f"""
        SELECT COUNT(*) AS c FROM review_checks rc
        WHERE rc.run_id IN ({ph}) AND rc.status = 'passed'
        """,
        run_ids,
    ).fetchone()["c"]
    if passed_any < 1:
        raise AssertionError("ожидался хотя бы один review_check со status=passed")

    fc = conn.execute(
        "SELECT COUNT(*) AS c FROM file_changes WHERE work_item_id = ?",
        (atom_id,),
    ).fetchone()["c"]
    if fc < 1:
        if _env_qwen_dry_run():
            raise AssertionError(
                f"file_changes: ожидалось >=1 (dry-run placeholder), получено {fc}"
            )
        # Wet: CLI мог завершиться без изменений файлов в песочнице — не блокируем happy-path.

    q = conn.execute(
        "SELECT COUNT(*) AS c FROM work_item_queue WHERE work_item_id = ?",
        (atom_id,),
    ).fetchone()["c"]
    if q != 0:
        raise AssertionError(f"work_item_queue по атому должна быть пуста, строк: {q}")

    if check_events:
        _require_event_count(conn, work_item_id=atom_id, event_type=EventType.TASK_CREATED, min_count=1)
        _require_event_count(conn, work_item_id=atom_id, event_type=EventType.REVIEW_PASSED, min_count=1)
        for r in runs:
            _assert_run_started_completed(conn, r["id"], require_completed=True)


def assert_rejected_atom(conn, atom_id: str, *, check_events: bool = True) -> None:
    """Атом после ревью с blocking failure (дальше возможна эскалация и stub-судья)."""
    st = conn.execute(
        "SELECT status FROM work_items WHERE id = ?",
        (atom_id,),
    ).fetchone()
    if not st:
        raise AssertionError(f"нет work_item {atom_id}")
    if st["status"] == WorkItemStatus.DONE.value:
        raise AssertionError("ветка с отказом ревью: атом не должен оказаться в done")

    rev_run = conn.execute(
        """
        SELECT id, status FROM runs
        WHERE work_item_id = ? AND role = 'reviewer'
        ORDER BY started_at DESC LIMIT 1
        """,
        (atom_id,),
    ).fetchone()
    if not rev_run or rev_run["status"] != "completed":
        raise AssertionError("ожидался завершённый прогон reviewer")

    bad = conn.execute(
        """
        SELECT COUNT(*) AS c FROM review_checks rc
        JOIN runs r ON r.id = rc.run_id
        WHERE r.work_item_id = ? AND rc.status = 'failed' AND rc.is_blocking = 1
        """,
        (atom_id,),
    ).fetchone()["c"]
    if bad < 1:
        raise AssertionError("ожидался хотя бы один review_check failed + is_blocking=1")

    if check_events:
        _require_event_count(conn, work_item_id=atom_id, event_type=EventType.REVIEW_REJECTED, min_count=1)


def assert_epic_mixed(
    conn,
    epic_id: str,
    ok_atom_id: str,
    bad_atom_id: str,
    *,
    check_events: bool = True,
) -> None:
    """Эпик не завершён, один атом happy, второй rejected."""
    st = conn.execute(
        "SELECT status FROM work_items WHERE id = ?",
        (epic_id,),
    ).fetchone()
    if not st:
        raise AssertionError(f"нет epic {epic_id}")
    if st["status"] == WorkItemStatus.DONE.value:
        raise AssertionError("epic не должен быть done при смешанных детях")

    _require_event_count(conn, work_item_id=epic_id, event_type=EventType.TASK_CREATED, min_count=1)
    _require_event_count(conn, work_item_id=ok_atom_id, event_type=EventType.TASK_CREATED, min_count=1)
    _require_event_count(conn, work_item_id=bad_atom_id, event_type=EventType.TASK_CREATED, min_count=1)

    assert_happy_atom(conn, ok_atom_id, check_events=check_events)
    assert_rejected_atom(conn, bad_atom_id, check_events=check_events)


def assert_forge_qwen_runner_step(conn, atom_id: str) -> None:
    """Forge-прогон оставил шаг ``run_steps`` с summary=qwen_cli_runner."""
    n = conn.execute(
        """
        SELECT COUNT(*) AS c FROM run_steps rs
        JOIN runs r ON r.id = rs.run_id
        WHERE r.work_item_id = ? AND r.role = 'forge' AND rs.summary = 'qwen_cli_runner'
        """,
        (atom_id,),
    ).fetchone()["c"]
    if n < 1:
        raise AssertionError(
            "ожидался run_steps с summary=qwen_cli_runner для forge run (qwen_cli_runner)"
        )


def assert_forge_run_completed_with_qwen_ok(conn, atom_id: str) -> None:
    """Forge-прогон завершён как ``completed``; в шаге ``qwen_cli_runner`` payload с ``ok: true``."""
    fr = conn.execute(
        """
        SELECT id, status FROM runs
        WHERE work_item_id = ? AND role = 'forge' AND run_type = 'implement'
        ORDER BY started_at ASC LIMIT 1
        """,
        (atom_id,),
    ).fetchone()
    if not fr:
        raise AssertionError("нет forge/implement run для атома")
    if fr["status"] != "completed":
        raise AssertionError(
            f"forge run ожидался completed, получено status={fr['status']}"
        )
    rid = fr["id"]
    row = conn.execute(
        """
        SELECT payload FROM run_steps
        WHERE run_id = ? AND summary = 'qwen_cli_runner'
        ORDER BY step_no LIMIT 1
        """,
        (rid,),
    ).fetchone()
    if not row:
        raise AssertionError("нет run_steps summary=qwen_cli_runner")
    try:
        payload = json.loads(row["payload"])
    except json.JSONDecodeError as e:
        raise AssertionError(f"run_steps payload JSON: {e}") from e
    if not payload.get("ok"):
        raise AssertionError(
            f"ожидался ForgeResult.ok в tool_result (payload.ok), получено {payload!r}"
        )


def assert_qwen_run_invocation_logged(conn, atom_id: str) -> None:
    """В ``event_log`` есть ``qwen.run.invocation`` по forge ``run_id`` (вызов ``run_qwen_cli``)."""
    fr = conn.execute(
        """
        SELECT id FROM runs
        WHERE work_item_id = ? AND role = 'forge' AND run_type = 'implement'
        ORDER BY started_at ASC LIMIT 1
        """,
        (atom_id,),
    ).fetchone()
    if not fr:
        raise AssertionError("нет forge/implement run для атома")
    rid = fr["id"]
    n = conn.execute(
        """
        SELECT COUNT(*) AS c FROM event_log
        WHERE entity_id = ? AND event_type = ?
        """,
        (rid, EventType.QWEN_RUN_INVOCATION.value),
    ).fetchone()["c"]
    if n < 1:
        raise AssertionError(
            f"ожидался event_type={EventType.QWEN_RUN_INVOCATION.value} для run_id={rid}"
        )


def assert_qwen_invocation_payload_matches_dry_setting(conn, atom_id: str) -> None:
    """
    Событие ``qwen.run.invocation`` несёт ``payload.dry_run``, как в ``run_qwen_cli``
    (тот же критерий, что ``_env_qwen_dry_run``): цепочка forge действительно прошла через раннер.
    """
    fr = conn.execute(
        """
        SELECT id FROM runs
        WHERE work_item_id = ? AND role = 'forge' AND run_type = 'implement'
        ORDER BY started_at ASC LIMIT 1
        """,
        (atom_id,),
    ).fetchone()
    if not fr:
        raise AssertionError("нет forge/implement run для атома")
    rid = fr["id"]
    row = conn.execute(
        """
        SELECT payload FROM event_log
        WHERE entity_id = ? AND event_type = ?
        ORDER BY id DESC LIMIT 1
        """,
        (rid, EventType.QWEN_RUN_INVOCATION.value),
    ).fetchone()
    if not row or not row["payload"]:
        raise AssertionError("qwen.run.invocation без payload")
    try:
        payload = json.loads(row["payload"])
    except json.JSONDecodeError as e:
        raise AssertionError(f"payload JSON: {e}") from e
    expect = _env_qwen_dry_run()
    if payload.get("dry_run") is not expect:
        raise AssertionError(
            f"payload.dry_run ожидалось {expect}, получено {payload!r} "
            f"(FACTORY_QWEN_DRY_RUN={os.environ.get('FACTORY_QWEN_DRY_RUN')!r})"
        )


def run_e2e_forge_qwen_dry() -> str:
    """
    E2E №3: один атом, аудит раннера + FSM forge.

    - Раннер по умолчанию dry-run, если переменная не задана; для «мокрого» прогона:
      ``FACTORY_QWEN_DRY_RUN=0``, ``qwen`` на PATH и ключи при необходимости.

    БД — временный файл, после прогона удаляется.
    """
    fd, raw = tempfile.mkstemp(prefix="factory_e2e_forge_qwen_", suffix=".db")
    os.close(fd)
    p = Path(raw)
    try:
        if os.environ.get("FACTORY_QWEN_DRY_RUN") is None:
            os.environ["FACTORY_QWEN_DRY_RUN"] = "1"
        atom_id = run_manual_e2e(p, override_qwen_dry=False)
        conn = sqlite3.connect(p)
        conn.row_factory = sqlite3.Row
        try:
            assert_qwen_run_invocation_logged(conn, atom_id)
            assert_qwen_invocation_payload_matches_dry_setting(conn, atom_id)
            assert_forge_run_completed_with_qwen_ok(conn, atom_id)
            assert_forge_qwen_runner_step(conn, atom_id)
            if _env_qwen_dry_run():
                assert_forge_has_file_write_steps(conn, atom_id, min_count=1)
            _require_event_count(
                conn,
                work_item_id=atom_id,
                event_type=EventType.FORGE_STARTED,
                min_count=1,
            )
            _require_event_count(
                conn,
                work_item_id=atom_id,
                event_type=EventType.FORGE_COMPLETED,
                min_count=1,
            )
            _require_event_count(
                conn,
                work_item_id=atom_id,
                event_type=EventType.REVIEW_PASSED,
                min_count=1,
            )
        finally:
            conn.close()
        print(f"E2E forge qwen dry: atom_id={atom_id} (temp db removed)")
        return atom_id
    finally:
        _unlink_quiet(p)


def assert_qwen_wet_failover_evidence(conn, atom_id: str) -> None:
    """После ``--e2e-qwen-wet-failover``: был rate limit и ротация (>=2 аккаунта в попытке)."""
    n = conn.execute(
        """
        SELECT COUNT(*) AS c FROM event_log
        WHERE work_item_id = ? AND event_type = ?
        """,
        (atom_id, EventType.ACCOUNT_RATE_LIMITED.value),
    ).fetchone()["c"]
    if n < 1:
        raise AssertionError(
            "failover E2E: ожидался event_log account.rate_limited по атому, получено "
            f"{n}"
        )
    row = conn.execute(
        """
        SELECT rs.payload FROM run_steps rs
        JOIN runs r ON r.id = rs.run_id
        WHERE r.work_item_id = ? AND r.role = 'forge' AND rs.summary = 'qwen_cli_runner'
        ORDER BY rs.step_no LIMIT 1
        """,
        (atom_id,),
    ).fetchone()
    if not row:
        raise AssertionError("failover E2E: нет run_steps qwen_cli_runner")
    try:
        payload = json.loads(row["payload"])
    except json.JSONDecodeError as e:
        raise AssertionError(f"payload JSON: {e}") from e
    at = payload.get("accounts_tried") or []
    if len(at) < 2:
        raise AssertionError(
            f"failover E2E: ожидалось >=2 accounts_tried, получено {at!r}"
        )


def assert_qwen_wet_forge_no_artifact_failure(conn, atom_id: str) -> None:
    """После ``--e2e-qwen-wet-forge-no-artifact``: wet без diff → forge_failed, не ``done``."""
    st = conn.execute(
        "SELECT status FROM work_items WHERE id = ?",
        (atom_id,),
    ).fetchone()
    if not st:
        raise AssertionError(f"нет work_item {atom_id}")
    if st["status"] == WorkItemStatus.DONE.value:
        raise AssertionError(
            "forge-no-artifact E2E: атом не должен быть done, получено done"
        )
    if st["status"] != WorkItemStatus.READY_FOR_WORK.value:
        raise AssertionError(
            f"forge-no-artifact E2E: ожидался ready_for_work после forge_failed, "
            f"получено {st['status']}"
        )
    fc = conn.execute(
        """
        SELECT COUNT(*) AS c FROM file_changes
        WHERE work_item_id = ? AND path = ?
        """,
        (atom_id, WET_EDIT_HELLO_PATH),
    ).fetchone()["c"]
    if fc != 0:
        raise AssertionError(
            f"forge-no-artifact E2E: не ожидались file_changes по hello, получено {fc}"
        )
    fr = conn.execute(
        """
        SELECT id, status, error_summary FROM runs
        WHERE work_item_id = ? AND role = 'forge' AND run_type = 'implement'
        ORDER BY started_at ASC LIMIT 1
        """,
        (atom_id,),
    ).fetchone()
    if not fr:
        raise AssertionError("нет forge run")
    if fr["status"] != "failed":
        raise AssertionError(
            f"forge run ожидался failed, получено {fr['status']}"
        )
    es = (fr["error_summary"] or "").lower()
    if "wet forge" not in es and "нет изменений" not in es:
        raise AssertionError(
            f"forge error_summary ожидалось про отсутствие правок по modify, "
            f"получено {fr['error_summary']!r}"
        )
    n_ev = conn.execute(
        """
        SELECT COUNT(*) AS c FROM event_log
        WHERE run_id = ? AND event_type = ?
        """,
        (fr["id"], EventType.RUN_FAILED_FORGE_NO_ARTIFACT.value),
    ).fetchone()["c"]
    if n_ev < 1:
        raise AssertionError(
            "ожидался event_log run.failed.forge_no_artifact для forge run"
        )
    n_fs = conn.execute(
        """
        SELECT COUNT(*) AS c FROM run_steps
        WHERE run_id = ? AND summary = 'forge_no_artifact'
        """,
        (fr["id"],),
    ).fetchone()["c"]
    if n_fs < 1:
        raise AssertionError("ожидался run_steps summary=forge_no_artifact")


def assert_qwen_wet_edit_hello_artifacts(conn, atom_id: str) -> None:
    """
    Жёсткая проверка wet-edit: ``file_changes`` и ``file_write`` по ``factory/hello_qwen.py``.
    Только для ``--e2e-qwen-wet-edit`` (``FACTORY_QWEN_DRY_RUN=0``).
    """
    path = WET_EDIT_HELLO_PATH
    fc = conn.execute(
        """
        SELECT change_type, new_hash, diff_summary FROM file_changes
        WHERE work_item_id = ? AND path = ?
        """,
        (atom_id, path),
    ).fetchall()
    if not fc:
        raise AssertionError(
            f"file_changes: ожидалась строка для path={path!r}, получено 0"
        )
    ok_row = None
    for row in fc:
        if row["change_type"] == "modify":
            ok_row = row
            break
    if ok_row is None:
        raise AssertionError(
            f"file_changes: ожидался change_type=modify для {path!r}, есть {fc!r}"
        )
    if not (ok_row["new_hash"] or "").strip():
        raise AssertionError("file_changes.new_hash не должен быть пустым после правки")

    fr = conn.execute(
        """
        SELECT id FROM runs
        WHERE work_item_id = ? AND role = 'forge' AND run_type = 'implement'
        ORDER BY started_at ASC LIMIT 1
        """,
        (atom_id,),
    ).fetchone()
    if not fr:
        raise AssertionError("нет forge/implement run")
    rid = fr["id"]
    n = conn.execute(
        """
        SELECT COUNT(*) AS c FROM run_steps
        WHERE run_id = ? AND step_kind = 'file_write' AND summary = ?
        """,
        (rid, path),
    ).fetchone()["c"]
    if n < 1:
        raise AssertionError(
            f"ожидался run_steps file_write для path={path!r}, run_id={rid}"
        )


def assert_forge_has_file_write_steps(conn, atom_id: str, *, min_count: int = 1) -> None:
    """Есть ``run_steps`` с ``step_kind=file_write`` (diff с песочницы)."""
    fr = conn.execute(
        """
        SELECT id FROM runs
        WHERE work_item_id = ? AND role = 'forge' AND run_type = 'implement'
        ORDER BY started_at ASC LIMIT 1
        """,
        (atom_id,),
    ).fetchone()
    if not fr:
        raise AssertionError("нет forge/implement run")
    rid = fr["id"]
    n = conn.execute(
        """
        SELECT COUNT(*) AS c FROM run_steps
        WHERE run_id = ? AND step_kind = 'file_write'
        """,
        (rid,),
    ).fetchone()["c"]
    if n < min_count:
        raise AssertionError(
            f"ожидалось >= {min_count} шагов file_write для {rid}, получено {n}"
        )


def assert_forge_worker_prompt_llm_steps(conn, atom_id: str) -> None:
    """У forge-прогона есть шаги ``prompt``, ``llm_reply`` и ``qwen_cli_runner`` (tool_result)."""
    fr = conn.execute(
        """
        SELECT id FROM runs
        WHERE work_item_id = ? AND role = 'forge' AND run_type = 'implement'
        ORDER BY started_at ASC LIMIT 1
        """,
        (atom_id,),
    ).fetchone()
    if not fr:
        raise AssertionError("нет forge/implement run")
    rid = fr["id"]
    rows = conn.execute(
        """
        SELECT step_kind, summary FROM run_steps WHERE run_id = ? ORDER BY step_no
        """,
        (rid,),
    ).fetchall()
    kinds = [r["step_kind"] for r in rows]
    if "prompt" not in kinds:
        raise AssertionError(
            f"ожидался step_kind=prompt в run_steps для {rid}, есть {kinds}"
        )
    if "llm_reply" not in kinds:
        raise AssertionError(
            f"ожидался step_kind=llm_reply в run_steps для {rid}, есть {kinds}"
        )
    summaries = [r["summary"] for r in rows]
    if "qwen_cli_runner" not in summaries:
        raise AssertionError(
            f"ожидался summary=qwen_cli_runner в run_steps для {rid}, есть {summaries}"
        )


def run_e2e_live() -> str:
    """
    E2E «live» контур: тот же сценарий, что ``--e2e-forge-qwen-dry``, плюс проверка
    ``forge_worker``: ``prompt`` / ``llm_reply`` / ``qwen_cli_runner`` в ``run_steps``.
    По умолчанию ``FACTORY_QWEN_DRY_RUN=1`` (без subprocess).
    """
    fd, raw = tempfile.mkstemp(prefix="factory_e2e_live_", suffix=".db")
    os.close(fd)
    p = Path(raw)
    try:
        if os.environ.get("FACTORY_QWEN_DRY_RUN") is None:
            os.environ["FACTORY_QWEN_DRY_RUN"] = "1"
        atom_id = run_manual_e2e(p, override_qwen_dry=False)
        conn = sqlite3.connect(p)
        conn.row_factory = sqlite3.Row
        try:
            assert_forge_worker_prompt_llm_steps(conn, atom_id)
            if _env_qwen_dry_run():
                assert_forge_has_file_write_steps(conn, atom_id, min_count=1)
            assert_qwen_run_invocation_logged(conn, atom_id)
            assert_qwen_invocation_payload_matches_dry_setting(conn, atom_id)
            _require_event_count(
                conn,
                work_item_id=atom_id,
                event_type=EventType.FORGE_STARTED,
                min_count=1,
            )
            _require_event_count(
                conn,
                work_item_id=atom_id,
                event_type=EventType.FORGE_COMPLETED,
                min_count=1,
            )
            fc = conn.execute(
                "SELECT COUNT(*) AS c FROM file_changes WHERE work_item_id = ?",
                (atom_id,),
            ).fetchone()["c"]
            if fc < 1 and _env_qwen_dry_run():
                raise AssertionError("ожидались file_changes после forge (dry-run placeholder)")
        finally:
            conn.close()
        print(f"E2E live: atom_id={atom_id}")
        return atom_id
    finally:
        _unlink_quiet(p)


def run_e2e_qwen_wet_edit() -> str:
    """
    Только при ``FACTORY_QWEN_DRY_RUN=0``: один атом, один файл ``factory/hello_qwen.py``,
    явная задача на правку — ожидается реальный diff и ``file_changes`` / ``file_write``.

    Запуск из каталога ``proekt/`` (``FACTORY_WORKSPACE_ROOT``), файл-заглушка должен существовать в репо.

    Интеграция с дашбордом: ``FACTORY_E2E_USE_ENV_DB=1`` — писать в ту же БД, что и
    ``FACTORY_DB_PATH`` / ``python -m factory --dashboard-api`` (файл не удаляется после прогона).
    """
    if _env_qwen_dry_run():
        raise RuntimeError(
            "Отказываюсь: --e2e-qwen-wet-edit требует FACTORY_QWEN_DRY_RUN=0, "
            "бинарник qwen и ключи. Для CI используйте dry-сценарии."
        )

    use_env_db = os.environ.get("FACTORY_E2E_USE_ENV_DB", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if use_env_db:
        p = resolve_db_path()
        _unlink_quiet(p)
    else:
        fd, raw = tempfile.mkstemp(prefix="factory_e2e_qwen_wet_edit_", suffix=".db")
        os.close(fd)
        p = Path(raw)
    try:
        if not use_env_db:
            _unlink_quiet(p)
        f = wire(p)
        conn = f["conn"]
        sm = f["sm"]
        orch = f["orchestrator"]
        actions = f["actions"]
        ops = WorkItemOps(conn, f["logger"])

        _vid, atom_id = drive_wet_hello_atom_to_ready_for_work(
            conn, sm, orch, actions, ops
        )

        orch.tick()

        st = conn.execute(
            "SELECT status FROM work_items WHERE id = ?",
            (atom_id,),
        ).fetchone()["status"]
        if st != WorkItemStatus.DONE.value:
            raise RuntimeError(f"Ожидался done, получено {st}")

        assert_forge_qwen_runner_step(conn, atom_id)
        assert_happy_atom(conn, atom_id, check_events=True)
        assert_qwen_wet_edit_hello_artifacts(conn, atom_id)

        print_trace_summary(conn, atom_id)
        print(f"E2E qwen-wet-edit: atom_id={atom_id} db={p.resolve()}")
        return atom_id
    finally:
        if not use_env_db:
            _unlink_quiet(p)


def _assert_planner_e2e_structure(conn, vision_id: str) -> None:
    """Проверка дерева после Planner: уровни, файлы у атомов, цепочка к vision."""
    for kind in ("epic", "story", "task", "atom"):
        n = conn.execute(
            """
            SELECT COUNT(*) AS c FROM work_items
            WHERE root_id = ? AND LOWER(kind) = ?
            """,
            (vision_id, kind),
        ).fetchone()["c"]
        if n < 1:
            raise AssertionError(f"E2E planner: ожидался хотя бы один {kind}, получено {n}")

    atoms = conn.execute(
        """
        SELECT id FROM work_items
        WHERE root_id = ? AND LOWER(kind) = 'atom'
        """,
        (vision_id,),
    ).fetchall()
    for a in atoms:
        aid = a["id"]
        fc = conn.execute(
            "SELECT COUNT(*) AS c FROM work_item_files WHERE work_item_id = ?",
            (aid,),
        ).fetchone()["c"]
        if fc < 1:
            raise AssertionError(f"E2E planner: у атома {aid} нет work_item_files")

    kinds_up = ("atom", "task", "story", "epic", "vision")
    for a in atoms:
        cur = a["id"]
        for expected in kinds_up:
            row = conn.execute(
                "SELECT id, parent_id, kind, root_id FROM work_items WHERE id = ?",
                (cur,),
            ).fetchone()
            if not row:
                raise AssertionError(f"E2E planner: нет узла {cur}")
            if (row["kind"] or "").lower() != expected:
                raise AssertionError(
                    f"E2E planner: цепочка kind: ожидалось {expected}, "
                    f"получено {row['kind']!r} у {cur}"
                )
            if row["root_id"] != vision_id:
                raise AssertionError(
                    f"E2E planner: root_id {row['root_id']!r} != vision {vision_id!r}"
                )
            if expected == "vision":
                if row["id"] != vision_id:
                    raise AssertionError("E2E planner: корень не совпадает с vision_id")
                break
            parent = row["parent_id"]
            if not parent:
                raise AssertionError(f"E2E planner: оборвана цепочка на {expected}")
            cur = parent


def run_e2e_planner() -> str:
    """
    ``FACTORY_QWEN_DRY_RUN=0``: один Vision, вызов :class:`Planner`, проверка БД.

    Не проверяет «логичность» декомпозиции — только pipeline и целостность схемы.
    """
    if _env_qwen_dry_run():
        raise RuntimeError(
            "Отказываюсь: --e2e-planner требует FACTORY_QWEN_DRY_RUN=0, qwen и ключи."
        )

    fd, raw = tempfile.mkstemp(prefix="factory_e2e_planner_", suffix=".db")
    os.close(fd)
    p = Path(raw)
    try:
        _unlink_quiet(p)
        f = wire(p)
        conn = f["conn"]
        ops = WorkItemOps(conn, f["logger"])
        vid = ops.create_vision(
            "Тестовый Vision",
            "Добавить функцию hello в factory/hello_qwen.py",
        )
        planner = Planner(conn, f["logger"], ops, f["accounts"])
        summary = planner.decompose_vision(vid)
        if not summary.get("ok"):
            raise RuntimeError(
                f"planner.decompose_vision failed: {summary.get('error')!r}\n"
                f"raw_preview={summary.get('raw_preview')!r}"
            )

        _assert_planner_e2e_structure(conn, vid)
        print(render_vision_tree(conn, vid))
        print(
            f"E2E planner: vision_id={vid} db={p.resolve()} "
            f"epics={summary['epics']} stories={summary['stories']} "
            f"tasks={summary['tasks']} atoms={summary['atoms']}"
        )
        return vid
    finally:
        _unlink_quiet(p)


def run_e2e_planner_forge() -> str:
    """
    Planner → атом с ``factory/hello_qwen.py`` → маркировка готовности к кузнице → один прогон
    ``execute_run_next_atom`` (оркестратор + forge-worker + Qwen wet), проверки как у ``--e2e-qwen-wet-edit``.
    """
    if _env_qwen_dry_run():
        raise RuntimeError(
            "Отказываюсь: --e2e-planner-forge требует FACTORY_QWEN_DRY_RUN=0, qwen и ключи."
        )
    wr = Path(__file__).resolve().parent.parent
    if not os.environ.get("FACTORY_WORKSPACE_ROOT"):
        os.environ["FACTORY_WORKSPACE_ROOT"] = str(wr)

    fd, raw = tempfile.mkstemp(prefix="factory_e2e_planner_forge_", suffix=".db")
    os.close(fd)
    p = Path(raw)
    try:
        _unlink_quiet(p)
        f = wire(p)
        conn = f["conn"]
        ops = WorkItemOps(conn, f["logger"])
        vid = ops.create_vision(
            "Тестовый Vision (planner+forge)",
            (
                "Добавить функцию hello в factory/hello_qwen.py, чтобы она возвращала строку "
                '"Hello from Qwen"; не трогать другие файлы. Один атом — правка только этого пути.'
            ),
        )
        planner = Planner(conn, f["logger"], ops, f["accounts"])
        summary = planner.decompose_vision(vid)
        if not summary.get("ok"):
            raise RuntimeError(
                f"planner.decompose_vision failed: {summary.get('error')!r}\n"
                f"raw_preview={summary.get('raw_preview')!r}"
            )

        _assert_planner_e2e_structure(conn, vid)

        atom_row = conn.execute(
            """
            SELECT wi.id FROM work_items wi
            JOIN work_item_files wif ON wif.work_item_id = wi.id
            WHERE wi.root_id = ? AND LOWER(wi.kind) = 'atom'
              AND (
                LOWER(COALESCE(wif.path, '')) LIKE '%hello_qwen.py%'
                OR LOWER(TRIM(COALESCE(wif.path, ''))) = LOWER(?)
              )
            ORDER BY wi.priority ASC, wi.created_at ASC, wi.id ASC
            LIMIT 1
            """,
            (vid, WET_EDIT_HELLO_PATH),
        ).fetchone()
        if not atom_row:
            raise AssertionError(
                "E2E planner+forge: нет атома с файлом hello_qwen.py — проверьте ответ планировщика"
            )
        atom_id = atom_row["id"]

        mark_atom_ready_for_forge(
            conn, f["sm"], atom_id, orchestrator=f["orchestrator"]
        )

        atom_id2, ast, rst = execute_run_next_atom(f)
        if atom_id2 != atom_id:
            raise AssertionError(
                f"E2E planner+forge: ожидался выбранный атом {atom_id}, получено {atom_id2!r}"
            )
        if ast != WorkItemStatus.DONE.value:
            raise RuntimeError(
                f"E2E planner+forge: ожидался atom done, получено atom_status={ast!r} "
                f"forge_implement_run_status={rst!r}"
            )

        assert_forge_qwen_runner_step(conn, atom_id)
        assert_happy_atom(conn, atom_id, check_events=True)
        assert_qwen_wet_edit_hello_artifacts(conn, atom_id)

        tree = render_vision_tree(conn, vid)
        print(tree)
        print(
            f"E2E planner+forge: vision_id={vid} atom_id={atom_id} "
            f"atom_status={ast} forge_implement_run_status={rst} db={p.resolve()}"
        )
        return vid
    finally:
        _unlink_quiet(p)


def run_e2e_qwen_wet_failover() -> str:
    """
    Wet-edit с **симуляцией rate limit на первом аккаунте**, затем реальный Qwen на втором.

    Требует: ``FACTORY_QWEN_DRY_RUN=0``, ``qwen``, минимум **два** аккаунта в ``config.ACCOUNTS``
    (``FACTORY_API_KEY_1`` + ``FACTORY_API_KEY_2`` или два OAuth-файла).

    Устанавливает ``FACTORY_QWEN_E2E_SIMULATE_RATE_LIMIT_ON_FIRST_CALL=1`` на время прогона.
    """
    if _env_qwen_dry_run():
        raise RuntimeError(
            "--e2e-qwen-wet-failover требует FACTORY_QWEN_DRY_RUN=0 и два API-аккаунта."
        )
    if len(factory_config.ACCOUNTS) < 2:
        raise RuntimeError(
            "Нужно минимум два аккаунта: задайте FACTORY_API_KEY_1 и FACTORY_API_KEY_2 "
            "(или два oauth_creds*.json), перезапустите процесс."
        )

    prev_rl = os.environ.get("FACTORY_QWEN_E2E_SIMULATE_RATE_LIMIT_ON_FIRST_CALL")
    os.environ["FACTORY_QWEN_E2E_SIMULATE_RATE_LIMIT_ON_FIRST_CALL"] = "1"
    os.environ.pop("FACTORY_QWEN_E2E_SIMULATE_OK_NO_FILE_CHANGE", None)
    reset_e2e_qwen_simulation_hooks()

    fd, raw = tempfile.mkstemp(prefix="factory_e2e_qwen_wet_failover_", suffix=".db")
    os.close(fd)
    p = Path(raw)
    try:
        _unlink_quiet(p)
        f = wire(p)
        conn = f["conn"]
        sm = f["sm"]
        orch = f["orchestrator"]
        actions = f["actions"]
        ops = WorkItemOps(conn, f["logger"])

        _vid, atom_id = drive_wet_hello_atom_to_ready_for_work(
            conn, sm, orch, actions, ops
        )

        orch.tick()

        st = conn.execute(
            "SELECT status FROM work_items WHERE id = ?",
            (atom_id,),
        ).fetchone()["status"]
        if st != WorkItemStatus.DONE.value:
            raise RuntimeError(f"Ожидался done после failover, получено {st}")

        assert_forge_qwen_runner_step(conn, atom_id)
        assert_happy_atom(conn, atom_id, check_events=True)
        assert_qwen_wet_failover_evidence(conn, atom_id)
        assert_qwen_wet_edit_hello_artifacts(conn, atom_id)

        print_trace_summary(conn, atom_id)
        print(f"E2E qwen-wet-failover: atom_id={atom_id} db={p.resolve()}")
        return atom_id
    finally:
        if prev_rl is None:
            os.environ.pop("FACTORY_QWEN_E2E_SIMULATE_RATE_LIMIT_ON_FIRST_CALL", None)
        else:
            os.environ["FACTORY_QWEN_E2E_SIMULATE_RATE_LIMIT_ON_FIRST_CALL"] = prev_rl
        _unlink_quiet(p)


def run_e2e_qwen_wet_forge_no_artifact() -> str:
    """
    Wet: Qwen «успешен», но **нет изменений объявленных modify-файлов** → ``forge_failed``,
    ``run.failed.forge_no_artifact``, атом остаётся в ``ready_for_work`` (не ``done``).

    Использует хук ``FACTORY_QWEN_E2E_SIMULATE_OK_NO_FILE_CHANGE=1`` (один вызов
    ``run_qwen_cli`` без subprocess, см. ``qwen_cli_runner``).
    """
    if _env_qwen_dry_run():
        raise RuntimeError(
            "--e2e-qwen-wet-forge-no-artifact требует FACTORY_QWEN_DRY_RUN=0."
        )

    prev_na = os.environ.get("FACTORY_QWEN_E2E_SIMULATE_OK_NO_FILE_CHANGE")
    os.environ["FACTORY_QWEN_E2E_SIMULATE_OK_NO_FILE_CHANGE"] = "1"
    os.environ.pop("FACTORY_QWEN_E2E_SIMULATE_RATE_LIMIT_ON_FIRST_CALL", None)
    reset_e2e_qwen_simulation_hooks()

    fd, raw = tempfile.mkstemp(prefix="factory_e2e_qwen_wet_no_art_", suffix=".db")
    os.close(fd)
    p = Path(raw)
    try:
        _unlink_quiet(p)
        f = wire(p)
        conn = f["conn"]
        sm = f["sm"]
        orch = f["orchestrator"]
        actions = f["actions"]
        ops = WorkItemOps(conn, f["logger"])

        _vid, atom_id = drive_wet_hello_atom_to_ready_for_work(
            conn, sm, orch, actions, ops
        )

        orch.tick()

        assert_qwen_wet_forge_no_artifact_failure(conn, atom_id)

        print_trace_summary(conn, atom_id)
        print(f"E2E qwen-wet-forge-no-artifact: atom_id={atom_id} db={p.resolve()}")
        return atom_id
    finally:
        if prev_na is None:
            os.environ.pop("FACTORY_QWEN_E2E_SIMULATE_OK_NO_FILE_CHANGE", None)
        else:
            os.environ["FACTORY_QWEN_E2E_SIMULATE_OK_NO_FILE_CHANGE"] = prev_na
        _unlink_quiet(p)


def assert_trace_integrity(conn, atom_id: str) -> None:
    """Совместимость: то же, что assert_happy_atom с проверкой EventType."""
    assert_happy_atom(conn, atom_id, check_events=True)


def print_trace_summary(conn, atom_id: str) -> None:
    print("\n=== TRACE SUMMARY (atom_id=%s) ===" % atom_id)

    ev = conn.execute(
        """
        SELECT COUNT(*) AS c FROM event_log
        WHERE work_item_id = ? OR entity_id = ?
        """,
        (atom_id, atom_id),
    ).fetchone()["c"]
    print(f"  event_log rows (wi_id or entity_id): {ev}")

    runs = conn.execute(
        """
        SELECT id, role, run_type, status FROM runs WHERE work_item_id = ?
        ORDER BY started_at
        """,
        (atom_id,),
    ).fetchall()
    print(f"  runs: {len(runs)}")
    for r in runs:
        print(f"    - {r['id'][:20]}... role={r['role']} type={r['run_type']} status={r['status']}")

    run_ids = [r["id"] for r in runs]
    if run_ids:
        ph = ",".join("?" * len(run_ids))
        rs = conn.execute(
            f"SELECT COUNT(*) AS c FROM run_steps WHERE run_id IN ({ph})",
            run_ids,
        ).fetchone()["c"]
        print(f"  run_steps (for those runs): {rs}")

        rc = conn.execute(
            f"SELECT COUNT(*) AS c FROM review_checks WHERE run_id IN ({ph})",
            run_ids,
        ).fetchone()["c"]
        print(f"  review_checks: {rc}")

    fc = conn.execute(
        "SELECT COUNT(*) AS c FROM file_changes WHERE work_item_id = ?",
        (atom_id,),
    ).fetchone()["c"]
    print(f"  file_changes: {fc}")

    q = conn.execute(
        "SELECT queue_name, lease_owner FROM work_item_queue WHERE work_item_id = ?",
        (atom_id,),
    ).fetchall()
    print(f"  work_item_queue rows: {len(q)}")
    for row in q:
        print(f"    - {row['queue_name']} lease={row['lease_owner']}")

    st = conn.execute(
        "SELECT status FROM work_items WHERE id = ?",
        (atom_id,),
    ).fetchone()
    print(f"  work_items.status: {st['status'] if st else 'MISSING'}")
    print("=== END TRACE ===\n")


def run_manual_e2e(
    db_path: Path | None = None,
    *,
    override_qwen_dry: bool = True,
) -> str:
    """
    Создаёт БД, прогоняет сценарий, печатает сводку. Возвращает atom_id.

    ``override_qwen_dry=True`` (по умолчанию): принудительно ``FACTORY_QWEN_DRY_RUN=1`` на время прогона.
    ``False``: не трогать окружение — для ``--e2e-forge-qwen-dry`` при ``FACTORY_QWEN_DRY_RUN=0`` в CI.
    """
    path = db_path or DEFAULT_MANUAL_DB
    _unlink_quiet(path)

    _prev_dry: str | None = None
    if override_qwen_dry:
        _prev_dry = os.environ.get("FACTORY_QWEN_DRY_RUN")
        os.environ["FACTORY_QWEN_DRY_RUN"] = "1"
    try:
        f = wire(path)
        conn = f["conn"]
        sm = f["sm"]
        orch = f["orchestrator"]
        actions = f["actions"]
        ops = WorkItemOps(conn, f["logger"])

        vid = ops.create_vision(
            "Tiny E2E vision",
            "Ручной сценарий: ветка -> atom -> forge -> review -> done",
            auto_commit=False,
        )
        ok, msg = sm.apply_transition(
            vid, "creator_submitted", actor_role=Role.CREATOR.value
        )
        if not ok:
            raise RuntimeError(f"creator_submitted: {msg}")
        conn.commit()

        orch.tick()  # planner: epic + planner_decomposed

        epic_row = conn.execute(
            "SELECT id FROM work_items WHERE parent_id = ? ORDER BY created_at LIMIT 1",
            (vid,),
        ).fetchone()
        if not epic_row:
            raise RuntimeError("После planner нет дочернего epic")
        epic_id = epic_row["id"]

        orch.tick()  # architect: комментарий к epic

        atom_id = ops.create_child(
            epic_id,
            WorkItemKind.ATOM.value,
            "Trace atom",
            "E2E atom",
            files=[
                {
                    "path": "factory/models.py",
                    "intent": "modify",
                    "description": "e2e touch",
                }
            ],
            auto_commit=False,
        )
        ok, msg = sm.apply_transition(
            atom_id,
            "creator_submitted",
            actor_role=Role.PLANNER.value,
        )
        if not ok:
            raise RuntimeError(f"atom creator_submitted: {msg}")

        ops.add_comment(
            atom_id,
            Role.ARCHITECT.value,
            "Architecture OK for tiny atom",
            comment_type=CommentType.ANALYSIS.value,
            auto_commit=False,
        )
        conn.commit()

        ok, msg = sm.apply_transition(
            atom_id,
            "ready_for_review",
            actor_role=Role.PLANNER.value,
        )
        if not ok:
            raise RuntimeError(f"ready_for_review: {msg}")
        actions.action_notify_judge(atom_id)
        conn.commit()

        orch.tick()  # judge -> ready_for_work + forge_inbox

        st = conn.execute(
            "SELECT status FROM work_items WHERE id = ?",
            (atom_id,),
        ).fetchone()["status"]
        if st != WorkItemStatus.READY_FOR_WORK.value:
            raise RuntimeError(f"Ожидался ready_for_work после judge, получено {st}")

        orch.tick()  # forge_started + qwen dry + review -> done

        st = conn.execute(
            "SELECT status FROM work_items WHERE id = ?",
            (atom_id,),
        ).fetchone()["status"]
        if st != WorkItemStatus.DONE.value:
            raise RuntimeError(f"Ожидался done после forge-worker+review, получено {st}")

        assert_forge_qwen_runner_step(conn, atom_id)
        assert_happy_atom(conn, atom_id, check_events=True)
        print_trace_summary(conn, atom_id)
        print(f"DB file (inspect): {path.resolve()}")
        print(f"atom_id: {atom_id}")
        return atom_id
    finally:
        if override_qwen_dry:
            if _prev_dry is None:
                os.environ.pop("FACTORY_QWEN_DRY_RUN", None)
            else:
                os.environ["FACTORY_QWEN_DRY_RUN"] = _prev_dry


def _run_atom_happy_to_done(
    conn,
    sm,
    orch,
    actions,
    ops: WorkItemOps,
    atom_id: str,
) -> None:
    """От draft-атома с файлами до done (forge-worker + qwen dry + reviewer)."""
    ok, msg = sm.apply_transition(
        atom_id,
        "creator_submitted",
        actor_role=Role.PLANNER.value,
    )
    if not ok:
        raise RuntimeError(f"atom creator_submitted: {msg}")

    ops.add_comment(
        atom_id,
        Role.ARCHITECT.value,
        "Architecture OK for atom",
        comment_type=CommentType.ANALYSIS.value,
        auto_commit=False,
    )
    conn.commit()

    ok, msg = sm.apply_transition(
        atom_id,
        "ready_for_review",
        actor_role=Role.PLANNER.value,
    )
    if not ok:
        raise RuntimeError(f"ready_for_review: {msg}")
    actions.action_notify_judge(atom_id)
    conn.commit()

    orch.tick()
    st = conn.execute(
        "SELECT status FROM work_items WHERE id = ?",
        (atom_id,),
    ).fetchone()["status"]
    if st != WorkItemStatus.READY_FOR_WORK.value:
        raise RuntimeError(f"Ожидался ready_for_work после judge, получено {st}")

    orch.tick()
    st = conn.execute(
        "SELECT status FROM work_items WHERE id = ?",
        (atom_id,),
    ).fetchone()["status"]
    if st != WorkItemStatus.DONE.value:
        raise RuntimeError(f"Ожидался done после forge-worker+review, получено {st}")


def _run_atom_bad_forge_then_review_reject(
    conn,
    sm,
    orch,
    actions,
    ops: WorkItemOps,
    atom_id: str,
) -> None:
    """Judge → forge-worker (dry) → review с принудительным отказом (для atom_bad)."""
    ok, msg = sm.apply_transition(
        atom_id,
        "creator_submitted",
        actor_role=Role.PLANNER.value,
    )
    if not ok:
        raise RuntimeError(f"atom creator_submitted: {msg}")

    ops.add_comment(
        atom_id,
        Role.ARCHITECT.value,
        "Architecture OK for atom",
        comment_type=CommentType.ANALYSIS.value,
        auto_commit=False,
    )
    conn.commit()

    ok, msg = sm.apply_transition(
        atom_id,
        "ready_for_review",
        actor_role=Role.PLANNER.value,
    )
    if not ok:
        raise RuntimeError(f"ready_for_review: {msg}")
    actions.action_notify_judge(atom_id)
    conn.commit()

    orch.tick()
    st = conn.execute(
        "SELECT status FROM work_items WHERE id = ?",
        (atom_id,),
    ).fetchone()["status"]
    if st != WorkItemStatus.READY_FOR_WORK.value:
        raise RuntimeError(f"Ожидался ready_for_work после judge, получено {st}")

    os.environ["FACTORY_REVIEW_FORCE_REJECT"] = "1"
    try:
        orch.tick()
    finally:
        os.environ.pop("FACTORY_REVIEW_FORCE_REJECT", None)
    # Далее возможен stub-судья в том же/следующем tick (ready_for_judge / ready_for_work).


def run_manual_e2e_two_atoms(db_path: Path | None = None) -> tuple[str, str, str]:
    """
    Epic + atom_ok (done) + atom_bad (review rejected / эскалация к судье).
    БД по умолчанию: factory_e2e_two_atoms.db
    """
    path = db_path or DEFAULT_TWO_ATOMS_DB
    _unlink_quiet(path)

    _prev_dry = os.environ.get("FACTORY_QWEN_DRY_RUN")
    os.environ["FACTORY_QWEN_DRY_RUN"] = "1"
    try:
        f = wire(path)
        conn = f["conn"]
        sm = f["sm"]
        orch = f["orchestrator"]
        actions = f["actions"]
        ops = WorkItemOps(conn, f["logger"])

        vid = ops.create_vision(
            "E2E#2 vision (two atoms)",
            "Частичный успех: один done, один review_rejected",
            auto_commit=False,
        )
        ok, msg = sm.apply_transition(
            vid, "creator_submitted", actor_role=Role.CREATOR.value
        )
        if not ok:
            raise RuntimeError(f"creator_submitted: {msg}")
        conn.commit()

        orch.tick()

        epic_row = conn.execute(
            "SELECT id FROM work_items WHERE parent_id = ? ORDER BY created_at LIMIT 1",
            (vid,),
        ).fetchone()
        if not epic_row:
            raise RuntimeError("После planner нет дочернего epic")
        epic_id = epic_row["id"]

        orch.tick()

        atom_ok = ops.create_child(
            epic_id,
            WorkItemKind.ATOM.value,
            "atom_ok",
            "OK path",
            files=[
                {
                    "path": "factory/ok.py",
                    "intent": "modify",
                    "description": "e2e ok",
                }
            ],
            auto_commit=False,
        )
        atom_bad = ops.create_child(
            epic_id,
            WorkItemKind.ATOM.value,
            "atom_bad",
            "Bad review path",
            files=[
                {
                    "path": "factory/bad.py",
                    "intent": "modify",
                    "description": "e2e bad",
                }
            ],
            auto_commit=False,
        )
        conn.execute(
            "UPDATE work_items SET priority = ? WHERE id = ?",
            (5, atom_ok),
        )
        conn.execute(
            "UPDATE work_items SET priority = ? WHERE id = ?",
            (15, atom_bad),
        )
        conn.commit()

        _run_atom_happy_to_done(conn, sm, orch, actions, ops, atom_ok)
        assert_forge_qwen_runner_step(conn, atom_ok)
        _run_atom_bad_forge_then_review_reject(
            conn, sm, orch, actions, ops, atom_bad
        )

        epic_st = conn.execute(
            "SELECT status FROM work_items WHERE id = ?",
            (epic_id,),
        ).fetchone()["status"]
        print(
            f"epic_id={epic_id} epic.status={epic_st} "
            f"atom_ok={atom_ok} atom_bad={atom_bad}"
        )

        assert_epic_mixed(conn, epic_id, atom_ok, atom_bad, check_events=True)
        print_trace_summary(conn, atom_ok)
        print_trace_summary(conn, atom_bad)
        print(f"DB file (inspect): {path.resolve()}")
        return epic_id, atom_ok, atom_bad
    finally:
        if _prev_dry is None:
            os.environ.pop("FACTORY_QWEN_DRY_RUN", None)
        else:
            os.environ["FACTORY_QWEN_DRY_RUN"] = _prev_dry
