"""Actions — побочные эффекты переходов FSM (Фаза 1)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .config import AccountManager
from .db import gen_id, payload_hash, stable_json_dumps
from .logging import FactoryLogger
from .models import EventType, QueueName, Role, RunType, Severity


class Actions:
    def __init__(
        self,
        conn: sqlite3.Connection,
        logger: FactoryLogger,
        account_manager: AccountManager,
    ) -> None:
        self.conn = conn
        self.logger = logger
        self.account_manager = account_manager

    def action_notify_planner(self, wi_id: str, **ctx: Any) -> None:
        self._enqueue(wi_id, QueueName.PLANNER_INBOX)

    def action_notify_judge(self, wi_id: str, **ctx: Any) -> None:
        self._enqueue(wi_id, QueueName.JUDGE_INBOX)

    def action_notify_architect(self, wi_id: str, **ctx: Any) -> None:
        """Очередь architect_inbox (дети после planner_decomposed; не путать с st_02)."""
        self._enqueue(wi_id, QueueName.ARCHITECT_INBOX)

    def action_enqueue_forge(self, wi_id: str, **ctx: Any) -> None:
        self._enqueue(wi_id, QueueName.FORGE_INBOX)

    def action_enqueue_reviewer(self, wi_id: str, **ctx: Any) -> None:
        self._enqueue(wi_id, QueueName.REVIEW_INBOX)

    def action_return_to_author(self, wi_id: str, **ctx: Any) -> None:
        wi = self.conn.execute(
            "SELECT creator_role FROM work_items WHERE id = ?",
            (wi_id,),
        ).fetchone()
        queue_map = {
            Role.ARCHITECT.value: QueueName.ARCHITECT_INBOX,
            Role.PLANNER.value: QueueName.PLANNER_INBOX,
            Role.HR.value: QueueName.HR_INBOX,
        }
        queue = queue_map.get(wi["creator_role"], QueueName.PLANNER_INBOX)
        self._enqueue(wi_id, queue)

    def action_start_forge_run(self, wi_id: str, **ctx: Any) -> None:
        """
        Блокировки + INSERT run (``queued``) + lease в той же транзакции, что и переход FSM.

        Вызов Qwen CLI выполняется **после** коммита перехода — в ``forge_worker.execute_forge_run``
        (см. ``run_forge_queued_runs`` в том же тике оркестратора): иначе долгий subprocess
        держал бы транзакцию SQLite открытой и блокировал бы БД.
        """
        run_id = ctx.get("run_id") or gen_id("run")
        ctx = {**ctx, "run_id": run_id}

        account = self.account_manager.get_active_account()
        agent_id = f"agent_{Role.FORGE.value}"
        agent_version = "unknown"
        agent_cfg = self.conn.execute(
            "SELECT model_name, prompt_version, config_json FROM agents WHERE id = ?",
            (agent_id,),
        ).fetchone()
        model_name_snapshot = agent_cfg["model_name"] if agent_cfg else None
        prompt_version = agent_cfg["prompt_version"] if agent_cfg else None
        model_params_json = agent_cfg["config_json"] if agent_cfg else None
        input_payload = {
            "work_item_id": wi_id,
            "run_type": RunType.IMPLEMENT.value,
            "trigger": "action_start_forge_run",
        }
        wi_retry_count_row = self.conn.execute(
            "SELECT retry_count FROM work_items WHERE id = ?",
            (wi_id,),
        ).fetchone()
        run_retry_count = int((wi_retry_count_row["retry_count"] if wi_retry_count_row else 0) or 0)

        # Сначала runs: file_locks.run_id REFERENCES runs(id) при включённых FK
        self.conn.execute(
            """
            INSERT INTO runs (
                id, work_item_id, agent_id, account_id, role, run_type, status,
                input_payload, input_hash, agent_version, prompt_version, model_name_snapshot, model_params_json, retry_count
            )
            VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                wi_id,
                agent_id,
                account["account_id"],
                Role.FORGE.value,
                RunType.IMPLEMENT.value,
                stable_json_dumps(input_payload),
                payload_hash(input_payload),
                agent_version,
                prompt_version,
                model_name_snapshot,
                model_params_json,
                run_retry_count,
            ),
        )

        self.action_acquire_file_locks(wi_id, **ctx)

        lease_until = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        self.conn.execute(
            """
            UPDATE work_item_queue SET lease_owner = ?, lease_until = ?
            WHERE work_item_id = ?
            """,
            (agent_id, lease_until, wi_id),
        )

        self.logger.log(
            EventType.RUN_STARTED,
            "run",
            run_id,
            f"Forge run for {wi_id}",
            work_item_id=wi_id,
            run_id=run_id,
            caused_by_type="run",
            caused_by_id=run_id,
            actor_role=Role.FORGE.value,
            account_id=account["account_id"],
            tags=["dispatch", "forge"],
        )

    def action_acquire_file_locks(self, wi_id: str, **ctx: Any) -> None:
        run_id = ctx.get("run_id")
        files = self.conn.execute(
            """
            SELECT path FROM work_item_files
            WHERE work_item_id = ? AND intent IN ('modify','create','delete','rename')
            """,
            (wi_id,),
        ).fetchall()

        for f in files:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO file_locks (path, work_item_id, run_id, lock_reason, expires_at)
                VALUES (?, ?, ?, 'forge_in_progress',
                        strftime('%Y-%m-%dT%H:%M:%f', 'now', '+1 hour'))
                """,
                (f["path"], wi_id, run_id),
            )

        self.logger.log(
            EventType.FORGE_STEP,
            "work_item",
            wi_id,
            f"Заблокировано {len(files)} файлов",
            work_item_id=wi_id,
            run_id=run_id,
            payload={"paths": [f["path"] for f in files], "step": "file_lock"},
        )

    def action_release_file_locks(self, wi_id: str, **ctx: Any) -> None:
        self.conn.execute(
            """
            UPDATE file_locks SET released_at = strftime('%Y-%m-%dT%H:%M:%f','now')
            WHERE work_item_id = ? AND released_at IS NULL
            """,
            (wi_id,),
        )

    def action_increment_retry(self, wi_id: str, **ctx: Any) -> None:
        self.conn.execute(
            "UPDATE work_items SET retry_count = retry_count + 1 WHERE id = ?",
            (wi_id,),
        )
        self._enqueue(wi_id, QueueName.FORGE_INBOX)

    def action_escalate_to_judge(self, wi_id: str, **ctx: Any) -> None:
        self.conn.execute(
            "UPDATE work_items SET needs_human_review = 1 WHERE id = ?",
            (wi_id,),
        )
        self._enqueue(wi_id, QueueName.JUDGE_INBOX)

    def action_commit_to_git(self, wi_id: str, **ctx: Any) -> None:
        self.action_release_file_locks(wi_id, **ctx)
        # Завершённые атомы не должны оставаться в work_item_queue (иначе будут висеть в depth KPI).
        self.conn.execute(
            "DELETE FROM work_item_queue WHERE work_item_id = ?",
            (wi_id,),
        )
        self.logger.log(
            EventType.TASK_STATUS_CHANGED,
            "work_item",
            wi_id,
            "Задача готова к коммиту в git",
            work_item_id=wi_id,
            severity=Severity.INFO,
            tags=["git", "done"],
            payload={"milestone": "git_commit_ready"},
        )

        parent = self.conn.execute(
            "SELECT parent_id FROM work_items WHERE id = ?",
            (wi_id,),
        ).fetchone()
        if parent and parent["parent_id"]:
            pid = parent["parent_id"]
            undone = self.conn.execute(
                """
                SELECT COUNT(*) AS c FROM work_items
                WHERE parent_id = ? AND status NOT IN (?, ?, ?)
                """,
                (pid, "done", "cancelled", "archived"),
            ).fetchone()["c"]
            if undone == 0:
                self.conn.execute(
                    """
                    INSERT INTO work_item_queue (work_item_id, queue_name)
                    VALUES (?, ?)
                    ON CONFLICT(work_item_id) DO UPDATE SET
                        queue_name = excluded.queue_name,
                        lease_owner = NULL,
                        lease_until = NULL
                    """,
                    (pid, QueueName.COMPLETION_INBOX.value),
                )

    def action_propagate_completion(self, wi_id: str, **ctx: Any) -> None:
        """
        Roll-up completion recursively via completion_inbox.

        Called on parent rollup transition (parent_complete → done). If the now-done item
        has a parent, and that parent has no remaining unfinished children, enqueue the parent
        into completion_inbox for the next tick.
        """
        event_name = ctx.get("event_name")
        new_status = ctx.get("new_status")
        if event_name != "parent_complete" or new_status != "done":
            return

        row = self.conn.execute(
            "SELECT parent_id, kind FROM work_items WHERE id = ?",
            (wi_id,),
        ).fetchone()
        if not row or not row["parent_id"]:
            return

        parent_id = row["parent_id"]
        undone = self.conn.execute(
            """
            SELECT COUNT(*) AS c FROM work_items
            WHERE parent_id = ? AND status NOT IN (?, ?, ?)
            """,
            (parent_id, "done", "cancelled", "archived"),
        ).fetchone()["c"]
        if undone != 0:
            return

        # Enqueue parent for rollup completion.
        self.conn.execute(
            """
            INSERT INTO work_item_queue (work_item_id, queue_name)
            VALUES (?, ?)
            ON CONFLICT(work_item_id) DO UPDATE SET
                queue_name = excluded.queue_name,
                lease_owner = NULL,
                lease_until = NULL
            """,
            (parent_id, QueueName.COMPLETION_INBOX.value),
        )
        self.logger.log(
            EventType.TASK_STATUS_CHANGED,
            "work_item",
            wi_id,
            f"Rollup: enqueued parent {parent_id} for completion",
            work_item_id=wi_id,
            severity=Severity.INFO,
            tags=["rollup", "completion_inbox"],
            payload={"sub": "rollup_propagate_completion", "parent_id": parent_id},
        )

    def action_create_review_comment(self, wi_id: str, **ctx: Any) -> None:
        run_id = ctx.get("run_id")
        if run_id:
            checks = self.conn.execute(
                """
                SELECT check_type, status, summary FROM review_checks
                WHERE run_id = ? AND status = 'failed'
                """,
                (run_id,),
            ).fetchall()
            body = "Ревью не пройдено:\n" + "\n".join(
                f"- [{c['check_type']}] {c['summary']}" for c in checks
            )
        else:
            body = "Ревью не пройдено (детали в прогоне)"

        self.conn.execute(
            """
            INSERT INTO comments (id, work_item_id, author_role, comment_type, body)
            VALUES (?, ?, 'reviewer', 'rejection', ?)
            """,
            (gen_id("cmt"), wi_id, body),
        )

    def action_build_judge_context(self, wi_id: str, **ctx: Any) -> None:
        self._enqueue(wi_id, QueueName.JUDGE_INBOX)

    def action_creator_cancelled_cleanup(self, wi_id: str, **ctx: Any) -> None:
        """После creator_cancelled: снять блокировки файлов и убрать из очередей."""
        self.action_release_file_locks(wi_id, **ctx)
        self.conn.execute(
            "DELETE FROM work_item_queue WHERE work_item_id = ?",
            (wi_id,),
        )

    def action_archive_finalize(self, wi_id: str, **ctx: Any) -> None:
        """После archive_sweep: очередь не нужна для архива."""
        self.conn.execute(
            "DELETE FROM work_item_queue WHERE work_item_id = ?",
            (wi_id,),
        )

    def action_cancel_children(self, wi_id: str, **ctx: Any) -> None:
        children = self.conn.execute(
            "SELECT id FROM work_items WHERE parent_id = ? AND status != 'cancelled'",
            (wi_id,),
        ).fetchall()
        for child in children:
            self.conn.execute(
                "UPDATE work_items SET status = 'cancelled' WHERE id = ?",
                (child["id"],),
            )
            self.logger.log(
                EventType.TASK_STATUS_CHANGED,
                "work_item",
                child["id"],
                f"Каскадная отмена (родитель {wi_id})",
                work_item_id=child["id"],
                payload={"cascade_cancel": True, "parent_id": wi_id},
            )

    def action_log_block_reason(self, wi_id: str, **ctx: Any) -> None:
        reason = ctx.get("reason", "Зависимость не удовлетворена")
        self.logger.log(
            EventType.TASK_STATUS_CHANGED,
            "work_item",
            wi_id,
            reason,
            work_item_id=wi_id,
            severity=Severity.WARN,
            payload={"blocked": True},
        )

    def _enqueue(self, wi_id: str, queue: QueueName) -> None:
        self.conn.execute(
            """
            INSERT INTO work_item_queue (work_item_id, queue_name)
            VALUES (?, ?)
            ON CONFLICT(work_item_id) DO UPDATE SET
                queue_name = excluded.queue_name,
                lease_owner = NULL,
                lease_until = NULL
            """,
            (wi_id, queue.value),
        )
        self.logger.log(
            EventType.TASK_ENQUEUED,
            "work_item",
            wi_id,
            f"task.enqueued {queue.value}",
            work_item_id=wi_id,
            payload={"queue_name": queue.value},
            tags=["queue", queue.value],
        )

    def resolve(self, action_name: str) -> Callable[..., None]:
        if not action_name:
            return lambda *a, **k: None
        # Support composite actions separated by ";"
        parts = [p.strip() for p in action_name.split(";") if p.strip()]
        if len(parts) > 1:
            fns = [self.resolve(p) for p in parts]
            def _composite(wi_id: str, **ctx: Any) -> None:
                for f in fns:
                    f(wi_id, **ctx)
            return _composite
        fn = getattr(self, action_name, None)
        if fn is None:
            raise ValueError(f"??????????? action: {action_name}")
        return fn
