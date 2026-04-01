"""Конкурентный доступ к SQLite: ensure_schema, event_log, restart-сценарий, modify fallback."""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from factory.db import ensure_schema, gen_id, get_connection, init_db
from factory.forge_sandbox import resolve_effective_work_item_files
from factory.logging import FactoryLogger
from factory.models import EventType


class TestConcurrentSchemaAndWrites(unittest.TestCase):
    def test_concurrent_ensure_schema(self) -> None:
        """Несколько потоков вызывают ensure_schema одновременно — без ошибок."""
        db_path = os.path.join(tempfile.mkdtemp(), "test_conc.db")
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                ensure_schema(db_path)
                conn = get_connection(db_path)
                tables = [
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                ]
                self.assertIn("work_items", tables)
                self.assertIn("improvement_candidates", tables)
                conn.close()
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60.0)

        self.assertEqual(errors, [], f"errors: {errors!r}")

    def test_concurrent_event_log_writes(self) -> None:
        """Несколько потоков пишут в event_log — без database is locked."""
        db_path = os.path.join(tempfile.mkdtemp(), "test_writes.db")
        ensure_schema(db_path)
        errors: list[BaseException] = []

        def writer(thread_id: int) -> None:
            try:
                conn = get_connection(db_path)
                for i in range(20):
                    conn.execute(
                        """
                        INSERT INTO event_log (
                            event_time, event_type, entity_type, entity_id,
                            severity, message, actor_role, payload
                        )
                        VALUES (?, 'test.write', 'system', ?, 'info', 'x', 'tester', '{}')
                        """,
                        (
                            datetime.now(timezone.utc).isoformat(),
                            f"ev_{thread_id}_{i}",
                        ),
                    )
                    conn.commit()
                    time.sleep(0.01)
                conn.close()
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60.0)

        self.assertEqual(errors, [], f"errors: {errors!r}")

        conn = get_connection(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM event_log WHERE event_type='test.write'"
        ).fetchone()[0]
        self.assertEqual(int(count), 100)
        conn.close()

    def test_restart_new_connection_while_old_writes(self) -> None:
        """Новое соединение при активной транзакции в другом — чтение не падает (WAL)."""
        db_path = os.path.join(tempfile.mkdtemp(), "test_restart.db")
        ensure_schema(db_path)

        conn1 = get_connection(db_path)
        conn1.execute("BEGIN IMMEDIATE")
        conn1.execute(
            """
            INSERT INTO event_log (
                event_time, event_type, entity_type, entity_id,
                severity, message, actor_role, payload
            )
            VALUES (?, 'test.restart', 'system', 'ev_restart_1', 'info', 'x', 'tester', '{}')
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )

        conn2 = get_connection(db_path)
        rows = conn2.execute("SELECT COUNT(*) AS c FROM event_log").fetchone()["c"]
        self.assertIsNotNone(rows)

        conn1.commit()
        conn1.close()

        conn2.execute(
            """
            INSERT INTO event_log (
                event_time, event_type, entity_type, entity_id,
                severity, message, actor_role, payload
            )
            VALUES (?, 'test.restart', 'system', 'ev_restart_2', 'info', 'x', 'tester', '{}')
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )
        conn2.commit()
        conn2.close()


class TestModifyMissingFile(unittest.TestCase):
    def test_modify_missing_file_logs_and_fallback(self) -> None:
        """modify на отсутствующий файл: предупреждение в журнале + effective intent=create."""
        os.environ.setdefault("FACTORY_API_KEY_1", "ut-modify-missing")
        os.environ.setdefault("FACTORY_API_NAME_1", "ut")
        os.environ.setdefault("FACTORY_API_LIMIT_1", "3000")
        from factory.tests.qwen_cli_test_env import reload_single_test_account

        reload_single_test_account()

        db_path = Path(tempfile.mkstemp(prefix="factory_modify_missing_", suffix=".db")[1])
        try:
            conn = init_db(db_path)
            wid = gen_id("atm")
            conn.execute(
                """
                INSERT INTO work_items (
                    id, parent_id, root_id, kind, title, description, status,
                    creator_role, planning_depth
                )
                VALUES (?, NULL, ?, 'atom', 't', 'd', 'draft', 'planner', 0)
                """,
                (wid, wid),
            )
            conn.execute(
                """
                INSERT INTO work_item_files (id, work_item_id, path, intent, description, required)
                VALUES (?, ?, 'nonexistent/path/for_modify.py', 'modify', '', 1)
                """,
                (gen_id("wif"), wid),
            )
            conn.commit()

            logger = FactoryLogger(conn)
            tmp_root = Path(tempfile.mkdtemp())
            eff = resolve_effective_work_item_files(
                conn, wid, tmp_root, logger=logger, run_id=None
            )
            conn.commit()

            row = conn.execute(
                "SELECT 1 FROM event_log WHERE event_type = ? LIMIT 1",
                (EventType.FORGE_MODIFY_MISSING_FILE.value,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(
                any(
                    (e.get("intent") == "create" and e.get("intent_override") == "modify")
                    for e in eff
                )
            )
            conn.close()
        finally:
            try:
                db_path.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
