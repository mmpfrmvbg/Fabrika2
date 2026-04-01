"""Сид демо-данных для introspect / improvements (ошибки журнала, отклонения, failed runs)."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from .config import resolve_db_path
from .db import gen_id, get_connection, init_db


def main() -> None:
    raw = None
    if len(sys.argv) > 1:
        raw = sys.argv[1]
    db_path = resolve_db_path(Path(raw) if raw else None)
    init_db(db_path)
    conn = get_connection(db_path)

    et = "forge.failed.demo"
    for i in range(5):
        wi = gen_id("wi")
        conn.execute(
            """
            INSERT INTO event_log (
                event_time, event_type, entity_type, entity_id, severity, message, payload,
                work_item_id, actor_role
            )
            VALUES (
                datetime('now', ?), ?, 'work_item', ?, 'error', ?, ?,
                ?, 'forge'
            )
            """,
            (
                f"-{i} minutes",
                et,
                wi,
                f"demo failure {i}",
                json.dumps({"i": i}),
                wi,
            ),
        )

    # rejected decisions (same reason_code ×2)
    for _ in range(2):
        wid = gen_id("wi")
        conn.execute(
            """
            INSERT INTO work_items (id, root_id, kind, title, status, creator_role, owner_role)
            VALUES (?, ?, 'atom', 'demo atom', 'draft', 'creator', 'creator')
            """,
            (wid, wid),
        )
        conn.execute(
            """
            INSERT INTO decisions (id, work_item_id, decision_role, verdict, reason_code)
            VALUES (?, ?, 'reviewer', 'rejected', 'quality')
            """,
            (gen_id("dec"), wid),
        )

    # failed forge run
    conn.execute(
        """
        INSERT OR IGNORE INTO agents (id, role, active) VALUES ('agent_forge', 'forge', 1)
        """
    )
    rw = gen_id("wi")
    conn.execute(
        """
        INSERT INTO work_items (id, root_id, kind, title, status, creator_role, owner_role)
        VALUES (?, ?, 'atom', 'demo failed run', 'draft', 'creator', 'creator')
        """,
        (rw, rw),
    )
    conn.execute(
        """
        INSERT INTO runs (id, work_item_id, agent_id, role, run_type, status, error_summary)
        VALUES (?, ?, 'agent_forge', 'forge', 'implement', 'failed', 'demo')
        """,
        (gen_id("run"), rw),
    )

    conn.commit()
    conn.close()
    print(f"seed_test_failures: OK db={db_path}")


if __name__ == "__main__":
    main()
