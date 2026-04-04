"""Создание и обновление work_items, комментариев, решений."""
import json
import sqlite3
from typing import Any

from .models import CommentType, EventType, Role
from .db import gen_id
from .logging import FactoryLogger


class WorkItemOps:
    """Операции создания и управления задачами."""

    def __init__(self, conn: sqlite3.Connection, logger: FactoryLogger):
        self.conn = conn
        self.logger = logger

    def create_vision(
        self, title: str, description: str | None = None, *, auto_commit: bool = True
    ) -> str:
        wi_id = gen_id("vis")
        self.conn.execute(
            """
            INSERT INTO work_items (id, root_id, kind, title, description,
                                    status, creator_role, owner_role)
            VALUES (?, ?, 'vision', ?, ?, 'draft', 'creator', 'creator')
            """,
            (wi_id, wi_id, title, description),
        )

        self.logger.log(
            EventType.TASK_CREATED,
            "work_item",
            wi_id,
            f"Vision создан: {title}",
            work_item_id=wi_id,
            actor_role=Role.CREATOR.value,
        )
        if auto_commit:
            self.conn.commit()
        return wi_id

    def create_child(
        self,
        parent_id: str,
        kind: str,
        title: str,
        description: str | None = None,
        creator_role: str = Role.PLANNER.value,
        files: list[dict[str, Any]] | None = None,
        *,
        auto_commit: bool = True,
    ) -> str:
        parent = self.conn.execute(
            "SELECT root_id, planning_depth FROM work_items WHERE id = ?",
            (parent_id,),
        ).fetchone()
        if not parent:
            raise ValueError(f"Родитель {parent_id} не найден")

        wi_id = gen_id(kind[:3])
        self.conn.execute(
            """
            INSERT INTO work_items
                (id, parent_id, root_id, kind, title, description,
                 status, creator_role, owner_role, planning_depth)
            VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)
            """,
            (
                wi_id,
                parent_id,
                parent["root_id"],
                kind,
                title,
                description,
                creator_role,
                creator_role,
                parent["planning_depth"] + 1,
            ),
        )

        if files:
            for f in files:
                self.conn.execute(
                    """
                    INSERT INTO work_item_files (id, work_item_id, path, intent, description)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        gen_id("wif"),
                        wi_id,
                        f["path"],
                        f["intent"],
                        f.get("description"),
                    ),
                )

        self.logger.log(
            EventType.TASK_CREATED,
            "work_item",
            wi_id,
            f"{kind} создан: {title} (родитель: {parent_id})",
            work_item_id=wi_id,
            actor_role=creator_role,
        )
        if auto_commit:
            self.conn.commit()
        return wi_id

    def add_comment(
        self,
        wi_id: str,
        author_role: str,
        body: str,
        comment_type: str = CommentType.NOTE.value,
        structured_payload: dict[str, Any] | None = None,
        *,
        auto_commit: bool = True,
    ) -> str:
        cmt_id = gen_id("cmt")
        self.conn.execute(
            """
            INSERT INTO comments (id, work_item_id, author_role, comment_type, body, structured_payload)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                cmt_id,
                wi_id,
                author_role,
                comment_type,
                body,
                json.dumps(structured_payload, ensure_ascii=False)
                if structured_payload
                else None,
            ),
        )
        if auto_commit:
            self.conn.commit()
        return cmt_id

    def record_decision(
        self,
        wi_id: str,
        decision_role: str,
        verdict: str,
        explanation: str | None = None,
        reason_code: str | None = None,
        suggested_fix: str | None = None,
        run_id: str | None = None,
        *,
        auto_commit: bool = True,
    ) -> str:
        dec_id = gen_id("dec")
        self.conn.execute(
            """
            INSERT INTO decisions
                (id, work_item_id, run_id, decision_role, verdict,
                 reason_code, explanation, suggested_fix)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dec_id,
                wi_id,
                run_id,
                decision_role,
                verdict,
                reason_code,
                explanation,
                suggested_fix,
            ),
        )
        if auto_commit:
            self.conn.commit()
        return dec_id
