"""Guards — предикаты переходов FSM (Фаза 1)."""

import sqlite3

from .models import RunType


class Guards:
    """Все guard-функции возвращают (ok: bool, reason: str)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def guard_has_children(self, wi_id: str) -> tuple[bool, str]:
        cnt = self.conn.execute(
            "SELECT COUNT(*) AS c FROM work_items WHERE parent_id = ?",
            (wi_id,),
        ).fetchone()["c"]
        if cnt > 0:
            return True, f"{cnt} подзадач создано"
        return False, "Нет подзадач — декомпозиция не выполнена"

    def guard_has_architect_comment(self, wi_id: str) -> tuple[bool, str]:
        cnt = self.conn.execute(
            """
            SELECT COUNT(*) AS c FROM comments
            WHERE work_item_id = ? AND author_role = 'architect'
            """,
            (wi_id,),
        ).fetchone()["c"]
        if cnt > 0:
            return True, "Комментарий архитектора есть"
        return False, "Нет комментария архитектора"

    def guard_has_revision_comment(self, wi_id: str) -> tuple[bool, str]:
        cnt = self.conn.execute(
            """
            SELECT COUNT(*) AS c FROM comments
            WHERE work_item_id = ? AND comment_type = 'note'
              AND created_at > (
                  SELECT MAX(created_at) FROM decisions
                  WHERE work_item_id = ? AND verdict = 'rejected'
              )
            """,
            (wi_id, wi_id),
        ).fetchone()["c"]
        if cnt > 0:
            return True, "Есть комментарий после отклонения"
        return False, "Нет ревизии после отклонения"

    def guard_files_lockable(self, wi_id: str) -> tuple[bool, str]:
        files = self.conn.execute(
            """
            SELECT wif.path FROM work_item_files wif
            WHERE wif.work_item_id = ? AND wif.intent IN ('modify','create','delete','rename')
            """,
            (wi_id,),
        ).fetchall()

        for f in files:
            lock = self.conn.execute(
                """
                SELECT work_item_id FROM file_locks
                WHERE path = ? AND released_at IS NULL
                """,
                (f["path"],),
            ).fetchone()
            if lock and lock["work_item_id"] != wi_id:
                return (
                    False,
                    f"Файл {f['path']} заблокирован задачей {lock['work_item_id']}",
                )

        return True, "Все файлы свободны"

    def guard_can_retry(self, wi_id: str) -> tuple[bool, str]:
        row = self.conn.execute(
            "SELECT retry_count, max_retries FROM work_items WHERE id = ?",
            (wi_id,),
        ).fetchone()
        if row["retry_count"] < row["max_retries"]:
            return True, f"Попытка {row['retry_count']+1}/{row['max_retries']}"
        return False, f"Исчерпаны все {row['max_retries']} попыток"

    def guard_over_retry_limit(self, wi_id: str) -> tuple[bool, str]:
        ok, reason = self.guard_can_retry(wi_id)
        return (not ok, reason)

    def guard_has_files_declared(self, wi_id: str) -> tuple[bool, str]:
        cnt = self.conn.execute(
            "SELECT COUNT(*) AS c FROM work_item_files WHERE work_item_id = ?",
            (wi_id,),
        ).fetchone()["c"]
        if cnt > 0:
            return True, f"{cnt} files declared"
        return False, "No files declared for this atom"

    def guard_ready_for_forge(self, wi_id: str) -> tuple[bool, str]:
        """
        Atom готов к кузнице, если:
        - файлы объявлены
        - ещё нет успешного forge implement (completed) по этому work item
          ИЛИ последний результат ревью = rejected (нужен retry после review_rejected → judge).
        """
        ok_files, reason_files = self.guard_has_files_declared(wi_id)
        if not ok_files:
            return False, reason_files

        rr = self.conn.execute(
            """
            SELECT verdict FROM review_results
            WHERE work_item_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (wi_id,),
        ).fetchone()
        if rr and (rr["verdict"] or "") == "rejected":
            return True, "Review rejected → allow forge retry"

        row = self.conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM runs
            WHERE work_item_id = ?
              AND role = 'forge'
              AND run_type = 'implement'
              AND status = 'completed'
            """,
            (wi_id,),
        ).fetchone()
        if int(row["c"]) > 0:
            return False, "Forge already completed for this atom"
        return True, "Files declared and no successful forge yet"

    def guard_has_review_approval(self, wi_id: str) -> tuple[bool, str]:
        """
        Финальный судья после ревью: допускаем judge_approved → done
        только если есть ReviewResult approved.
        """
        row = self.conn.execute(
            """
            SELECT verdict, created_at
            FROM review_results
            WHERE work_item_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (wi_id,),
        ).fetchone()
        if not row:
            return False, "No review result found"
        if (row["verdict"] or "") != "approved":
            return False, f"Latest review verdict is {row['verdict']}"
        return True, "Latest review verdict approved"

    def guard_has_file_changes(self, wi_id: str) -> tuple[bool, str]:
        """
        True if this work item has any captured file_changes from forge.

        Used to distinguish "judge rejected after we actually produced code" (retry → forge)
        from "judge rejected before any implementation existed" (return to author/planner).
        """
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM file_changes WHERE work_item_id = ?",
            (wi_id,),
        ).fetchone()
        c = int(row["c"]) if row else 0
        if c > 0:
            return True, f"{c} file_changes captured"
        return False, "No file_changes captured yet"

    def guard_all_children_done(self, wi_id: str) -> tuple[bool, str]:
        undone = self.conn.execute(
            """
            SELECT COUNT(*) AS c FROM work_items WHERE parent_id = ?
            AND status NOT IN (?, ?, ?)
            """,
            (wi_id, "done", "cancelled", "archived"),
        ).fetchone()["c"]
        if undone == 0:
            return True, "All children completed"
        return False, f"{undone} children not yet done"

    def guard_all_checks_passed(self, wi_id: str) -> tuple[bool, str]:
        run = self.conn.execute(
            """
            SELECT id FROM runs
            WHERE work_item_id = ? AND run_type = ?
            ORDER BY started_at DESC LIMIT 1
            """,
            (wi_id, RunType.REVIEW.value),
        ).fetchone()
        if not run:
            return False, "No review run found"

        failed = self.conn.execute(
            """
            SELECT COUNT(*) AS c FROM review_checks
            WHERE run_id = ? AND status = ? AND is_blocking = 1
            """,
            (run["id"], "failed"),
        ).fetchone()["c"]

        if failed > 0:
            return False, f"{failed} blocking check(s) failed"
        return True, "All blocking checks passed"

    def guard_cancellable_for_creator(self, wi_id: str) -> tuple[bool, str]:
        """Для creator_cancelled: не трогаем терминальные статусы."""
        row = self.conn.execute(
            "SELECT status FROM work_items WHERE id = ?", (wi_id,)
        ).fetchone()
        if not row:
            return False, "work_item not found"
        st = row["status"]
        if st in ("done", "cancelled", "archived"):
            return False, f"status {st} is terminal"
        return True, "cancellable"

    def guard_work_item_done(self, wi_id: str) -> tuple[bool, str]:
        """Для archive_sweep: только done → archived."""
        row = self.conn.execute(
            "SELECT status FROM work_items WHERE id = ?", (wi_id,)
        ).fetchone()
        if row and row["status"] == "done":
            return True, "done"
        return False, "work_item is not done"

    def guard_all_deps_met(self, wi_id: str) -> tuple[bool, str]:
        deps = self.conn.execute(
            """
            SELECT wil.dst_id, wi.status
            FROM work_item_links wil
            JOIN work_items wi ON wil.dst_id = wi.id
            WHERE wil.src_id = ? AND wil.link_type = 'depends_on'
            """,
            (wi_id,),
        ).fetchall()

        unmet = [d for d in deps if d["status"] != "done"]
        if unmet:
            ids = ", ".join(d["dst_id"] for d in unmet)
            return False, f"Не завершены зависимости: {ids}"
        return True, "Все зависимости удовлетворены"

    def resolve(self, guard_name: str):
        if not guard_name:
            raise ValueError("Пустой guard_name")
        fn = getattr(self, guard_name, None)
        if fn is None:
            raise ValueError(f"Неизвестный guard: {guard_name}")
        return fn

