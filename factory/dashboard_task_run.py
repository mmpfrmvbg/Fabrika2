"""Ручной запуск атома из дашборда: enqueue в ``forge_inbox`` (оркестратор подхватит сам)."""

from __future__ import annotations

from .composition import wire
from .config import resolve_db_path
from .dashboard_api_read import _normalize_kind
from .models import EventType, QueueName, Role, RunType, Severity


def _active_forge_run_count(conn, wi_id: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM runs
        WHERE work_item_id = ? AND role = ? AND run_type = ?
          AND status IN ('queued', 'running')
        """,
        (wi_id, Role.FORGE.value, RunType.IMPLEMENT.value),
    ).fetchone()
    return int(row["c"])


def _log_denied(logger, wi_id: str, reason: str, *, correlation_id: str | None = None) -> None:
    logger.log(
        EventType.DASHBOARD_TASK_RUN_DENIED,
        "work_item",
        wi_id,
        f"Dashboard run denied: {reason}",
        severity=Severity.WARN,
        work_item_id=wi_id,
        actor_role=Role.CREATOR.value,
        payload={"reason": reason, "correlation_id": correlation_id},
        tags=["dashboard", "run"],
    )


def accept_dashboard_task_run(wi_id: str, *, correlation_id: str | None = None) -> tuple[bool, dict, int]:
    """
    Проверки: существование work_item, kind → atom (в т.ч. atm_change), ``ready_for_work``,
    нет конфликтующего forge-run (иначе 409).

    Далее: пишем событие и гарантируем запись в ``work_item_queue`` с ``queue_name=forge_inbox``.
    Реальный forge запускает фоновый tick() оркестратора в api_server (или CLI оркестратор).

    Возвращает ``(success, body, http_status)``.
    """
    db_path = resolve_db_path()
    factory = wire(db_path)
    conn = factory["conn"]
    logger = factory["logger"]
    deny: tuple[bool, dict, int] | None = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id, kind, status, correlation_id FROM work_items WHERE id = ?",
            (wi_id,),
        ).fetchone()
        if not row:
            conn.commit()
            deny = (False, {"ok": False, "error": "work_item not found"}, 404)
        else:
            nk, _ = _normalize_kind(row["kind"] if isinstance(row["kind"], str) else None)
            if nk != "atom":
                _log_denied(
                    logger, wi_id, f"only atom can be run from dashboard (got kind={nk})", correlation_id=correlation_id
                )
                conn.commit()
                deny = (
                    False,
                    {"ok": False, "error": "only atom (or atm_change) supports dashboard run"},
                    400,
                )
            elif row["status"] == "in_progress":
                _log_denied(logger, wi_id, "forge already in progress for this atom", correlation_id=correlation_id)
                conn.commit()
                deny = (
                    False,
                    {"ok": False, "error": "forge run already in progress"},
                    409,
                )
            else:
                existing = conn.execute(
                    """
                    SELECT id
                    FROM runs
                    WHERE work_item_id = ?
                      AND status IN ('queued', 'running')
                    ORDER BY started_at DESC, id DESC
                    LIMIT 1
                    """,
                    (wi_id,),
                ).fetchone()
                if existing is not None:
                    existing_run_id = str(existing["id"])
                    _log_denied(
                        logger,
                        wi_id,
                        f"run already in progress (existing_run_id={existing_run_id})",
                        correlation_id=correlation_id,
                    )
                    conn.commit()
                    deny = (
                        False,
                        {
                            "ok": False,
                            "error": "run already in progress",
                            "existing_run_id": existing_run_id,
                        },
                        409,
                    )
            if deny is None and _active_forge_run_count(conn, wi_id) > 0:
                _log_denied(logger, wi_id, "forge run already queued or running", correlation_id=correlation_id)
                conn.commit()
                deny = (
                    False,
                    {"ok": False, "error": "forge run already queued or running"},
                    409,
                )
            elif deny is None and row["status"] != "ready_for_work":
                _log_denied(
                    logger,
                    wi_id,
                    f"status must be ready_for_work, got {row['status']}",
                    correlation_id=correlation_id,
                )
                conn.commit()
                deny = (
                    False,
                    {"ok": False, "error": f"status must be ready_for_work, got {row['status']}"},
                    400,
                )

        if deny is None:
            resolved_corr = correlation_id or (str(row["correlation_id"]) if row and row["correlation_id"] else None)
            if resolved_corr:
                conn.execute(
                    "UPDATE work_items SET correlation_id = ? WHERE id = ?",
                    (resolved_corr, wi_id),
                )
            logger.log(
                EventType.DASHBOARD_TASK_RUN_REQUESTED,
                "work_item",
                wi_id,
                "Dashboard: run requested (enqueue forge_inbox)",
                work_item_id=wi_id,
                actor_role=Role.CREATOR.value,
                payload={"source": "dashboard", "mode": "enqueue_only", "correlation_id": resolved_corr},
                tags=["dashboard", "run"],
            )
            conn.execute(
                """
                INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at, attempts)
                VALUES (?, ?, 10, datetime('now'), 0)
                ON CONFLICT(work_item_id) DO UPDATE SET
                    queue_name = excluded.queue_name,
                    lease_owner = NULL,
                    lease_until = NULL
                """,
                (wi_id, QueueName.FORGE_INBOX.value),
            )
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if deny is not None:
        return deny[0], deny[1], deny[2]

    return (
        True,
        {
            "ok": True,
            "status": "enqueued",
            "message": "accepted (enqueued to forge_inbox)",
        },
        200,
    )
