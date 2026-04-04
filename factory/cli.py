"""CLI: статус фабрики и демо."""
import logging
import os
import sys
from pathlib import Path

from .config import resolve_db_path
from .composition import wire
from .logging_config import configure_logging

_LOG = logging.getLogger(__name__)


def _flag_value(rest: list[str], flag: str) -> str | None:
    if flag not in rest:
        return None
    i = rest.index(flag)
    if i + 1 >= len(rest):
        return None
    nxt = rest[i + 1]
    if nxt.startswith("--"):
        return None
    return nxt


def cli_status(conn):
    """Печатает текущий статус фабрики."""
    _LOG.info("\n%s\n  FACTORY STATUS\n%s", "=" * 60, "=" * 60)

    states = conn.execute("SELECT * FROM system_state").fetchall()
    for s in states:
        _LOG.info("  %s %s", f"{s['key']:.<30}", s["value"])

    _LOG.info("\n  API ACCOUNTS:")
    accounts = conn.execute("SELECT * FROM v_api_usage_today").fetchall()
    for a in accounts:
        bar_len = 20
        used = a["requests_today"]
        limit = a["daily_limit"]
        filled = int((used / limit) * bar_len) if limit > 0 else 0
        bar = "#" * filled + "-" * (bar_len - filled)
        av = a["availability"]
        if av == "available":
            status_icon = "OK"
        elif av == "cooling_down":
            status_icon = "CD"
        else:
            status_icon = "XX"
        _LOG.info("    %s %8s [%s] %s/%s", status_icon, a["account_name"], bar, used, limit)

    _LOG.info("\n  WORK ITEMS:")
    stats = conn.execute(
        """
        SELECT status, COUNT(*) AS cnt
        FROM work_items
        GROUP BY status
        ORDER BY cnt DESC
        """
    ).fetchall()
    for s in stats:
        _LOG.info("    %s %s", f"{s['status']:.<25}", s["cnt"])

    _LOG.info("\n  QUEUES:")
    queues = conn.execute(
        """
        SELECT queue_name, COUNT(*) AS cnt,
               SUM(CASE WHEN lease_owner IS NOT NULL THEN 1 ELSE 0 END) AS leased
        FROM work_item_queue
        GROUP BY queue_name
        """
    ).fetchall()
    for q in queues:
        _LOG.info("    %s %s (leased: %s)", f"{q['queue_name']:.<25}", q["cnt"], q["leased"])

    active = conn.execute("SELECT COUNT(*) AS c FROM v_active_runs").fetchone()["c"]
    _LOG.info("\n  Active runs: %s", active)

    errors = conn.execute("SELECT COUNT(*) AS c FROM v_recent_errors").fetchone()["c"]
    _LOG.info("  Errors (last hour): %s", errors)

    _LOG.info("%s\n", "=" * 60)


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    configure_logging(level=logging.INFO)

    if argv and not argv[0].startswith("--"):
        db = resolve_db_path(Path(argv[0]))
        rest = argv[1:]
    else:
        db = resolve_db_path()
        rest = argv

    if "--create-vision" in rest:
        from .cli_vision import run_create_vision

        title = _flag_value(rest, "--title")
        if not title:
            _LOG.error("Ошибка: укажите --title \"...\"")
            sys.exit(2)
        desc = _flag_value(rest, "--description") or ""
        vid = run_create_vision(db, title, desc or None)
        _LOG.info("Vision %s created", vid)
        return

    if "--plan" in rest:
        from .planner import run_plan_command

        vision_id = _flag_value(rest, "--vision-id")
        if not vision_id:
            _LOG.error("Ошибка: укажите --vision-id <id>")
            sys.exit(2)
        rc = run_plan_command(db, vision_id.strip())
        sys.exit(rc)

    if "--e2e-planner" in rest:
        from .e2e_manual_trace import run_e2e_planner

        run_e2e_planner()
        _LOG.info("E2E OK: planner (prompt -> LLM -> parse -> work_items + work_item_files)")
        return

    if "--e2e-planner-forge" in rest:
        from .e2e_manual_trace import run_e2e_planner_forge

        run_e2e_planner_forge()
        _LOG.info(
            "E2E OK: planner + forge (decompose_vision → mark ready → run-next-atom path)"
        )
        return

    if "--add-atom" in rest:
        from .cli_vision import run_add_atom

        vision = _flag_value(rest, "--vision")
        title = _flag_value(rest, "--title")
        if not vision or not title:
            _LOG.error("Ошибка: нужны --vision <id> и --title \"...\"")
            sys.exit(2)
        desc = _flag_value(rest, "--description") or ""
        files = _flag_value(rest, "--files")
        if not files:
            _LOG.error("Ошибка: укажите --files path1,path2")
            sys.exit(2)
        fintent = _flag_value(rest, "--file-intent") or "modify"
        try:
            aid = run_add_atom(
                db,
                vision,
                title,
                desc or None,
                files_csv=files,
                file_intent=fintent,
            )
        except Exception as e:
            _LOG.error("%s", e)
            sys.exit(1)
        _LOG.info("%s", aid)
        return

    if "--seed-demo" in rest:
        from .seed_demo import run_seed_demo

        run_seed_demo(db)
        return

    if "--run-once" in rest:
        from .run_once import run_run_once

        run_run_once(db)
        return

    if "--mark-atom-ready-for-forge" in rest:
        from .forge_next_atom import mark_atom_ready_for_forge

        atom_id = _flag_value(rest, "--atom-id")
        if not atom_id:
            _LOG.error("Ошибка: укажите --atom-id <id>")
            sys.exit(2)
        f = wire(db)
        mark_atom_ready_for_forge(
            f["conn"],
            f["sm"],
            atom_id.strip(),
            orchestrator=f["orchestrator"],
        )
        _LOG.info("Atom %s: ready_for_work + forge_inbox (FSM + judge run)", atom_id.strip())
        return

    if "--run-next-atom" in rest:
        from .forge_next_atom import execute_run_next_atom

        if not os.environ.get("FACTORY_WORKSPACE_ROOT"):
            os.environ["FACTORY_WORKSPACE_ROOT"] = str(db.resolve().parent)
        f = wire(db)
        atom_id, ast, rst = execute_run_next_atom(f)
        if not atom_id:
            _LOG.info("Нет атомов, готовых к forge")
            return
        _LOG.info(
            f"run-next-atom: atom_id={atom_id} atom_status={ast} "
            f"forge_implement_run_status={rst}"
        )
        return

    if "--worker-loop" in rest:
        from .worker_loop import run_worker_loop

        run_worker_loop(db)
        return

    if "--e2e-golden" in rest:
        from .e2e_golden import run_e2e_review_to_done

        run_e2e_review_to_done()
        _LOG.info("E2E OK: review_to_done (temp DB)")
        return

    if "--e2e-chain" in rest:
        from .e2e_golden import run_e2e_chain_judge_forge_review_done

        run_e2e_chain_judge_forge_review_done()
        _LOG.info("E2E OK: chain judge -> forge -> review -> done (temp DB)")
        return

    if "--e2e-manual" in rest:
        from .e2e_manual_trace import run_manual_e2e

        run_manual_e2e()
        _LOG.info("E2E OK: manual tiny vision -> atom -> judge -> forge -> review -> done")
        return

    if "--e2e-two-atoms" in rest:
        from .e2e_manual_trace import run_manual_e2e_two_atoms

        run_manual_e2e_two_atoms()
        _LOG.info("E2E OK: epic + atom_ok (done) + atom_bad (review_rejected)")
        return

    if "--e2e-qwen-dry" in rest or "--e2e-forge-qwen-dry" in rest:
        from .e2e_manual_trace import run_e2e_forge_qwen_dry

        run_e2e_forge_qwen_dry()
        _LOG.info(
            "E2E OK: qwen dry (forge -> run_qwen_cli -> forge_completed -> review -> done)"
        )
        return

    if "--e2e-live" in rest:
        from .e2e_manual_trace import run_e2e_live

        run_e2e_live()
        _LOG.info(
            "E2E OK: live (forge_worker prompt/llm_reply + DRY_RUN + forge.completed + done)"
        )
        return

    if "--e2e-qwen-wet-edit" in rest:
        from .e2e_manual_trace import run_e2e_qwen_wet_edit

        run_e2e_qwen_wet_edit()
        _LOG.info(
            "E2E OK: qwen wet edit (hello_qwen.py file_changes + file_write, DRY_RUN=0)"
        )
        return

    if "--e2e-qwen-wet-failover" in rest:
        from .e2e_manual_trace import run_e2e_qwen_wet_failover

        run_e2e_qwen_wet_failover()
        _LOG.info(
            "E2E OK: qwen wet failover (rate-limit sim + second account, hello_qwen artifacts)"
        )
        return

    if "--e2e-qwen-wet-forge-no-artifact" in rest:
        from .e2e_manual_trace import run_e2e_qwen_wet_forge_no_artifact

        run_e2e_qwen_wet_forge_no_artifact()
        _LOG.info(
            "E2E OK: qwen wet forge no artifact (forge_failed, run.failed.forge_no_artifact)"
        )
        return

    if "--dashboard" in rest:
        os.environ.setdefault("FACTORY_API_PORT", "8000")
        if "--port" in rest:
            ix = rest.index("--port")
            if ix + 1 < len(rest):
                try:
                    os.environ["FACTORY_API_PORT"] = str(int(rest[ix + 1]))
                except ValueError:
                    pass
        from .api_server import main as run_readonly_api_server

        run_readonly_api_server()
        return

    if "--dashboard-legacy" in rest:
        os.environ.setdefault("FACTORY_DASHBOARD_PORT", "8420")
        if "--port" in rest:
            ix = rest.index("--port")
            if ix + 1 < len(rest):
                try:
                    os.environ["FACTORY_DASHBOARD_PORT"] = str(int(rest[ix + 1]))
                except ValueError:
                    pass
        from .dashboard_api import run_dashboard_api

        run_dashboard_api()
        return

    if "--dashboard-api" in rest or "--dash" in rest:
        if "--port" in rest:
            ix = rest.index("--port")
            if ix + 1 < len(rest):
                try:
                    os.environ["FACTORY_DASHBOARD_PORT"] = str(int(rest[ix + 1]))
                except ValueError:
                    pass
        from .dashboard_api import run_dashboard_api

        run_dashboard_api()
        return

    factory = wire(db)
    _LOG.info("Factory initialized: %s", db)
    cli_status(factory["conn"])

    if "--demo" in rest:
        ops = factory["ops"]
        sm = factory["sm"]

        vid = ops.create_vision(
            "Мобильное приложение для чат-бота",
            "React Native приложение с бекендом на FastAPI",
        )
        _LOG.info("Created vision: %s", vid)

        ok, msg = sm.apply_transition(vid, "creator_submitted", actor_role="creator")
        _LOG.info("Submitted: %s - %s", ok, msg)

        cli_status(factory["conn"])

    if "--run" in rest:
        _LOG.info("Starting orchestrator... (Ctrl+C to stop)")
        try:
            factory["orchestrator"].start()
        except KeyboardInterrupt:
            factory["orchestrator"].stop()
            _LOG.info("\nOrchestrator stopped.")


if __name__ == "__main__":
    main()
