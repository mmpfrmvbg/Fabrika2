from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
import subprocess
import sys


class TestConcurrentInitDb(unittest.TestCase):
    def setUp(self) -> None:
        # Ensure we have at least one account in ACCOUNTS for init_db() seed.
        os.environ.setdefault("FACTORY_API_KEY_1", "ut-concurrent-init")
        os.environ.setdefault("FACTORY_API_NAME_1", "ut")
        os.environ.setdefault("FACTORY_API_LIMIT_1", "3000")

        # Reload config/db to pick up env changes deterministically.
        from factory.tests.qwen_cli_test_env import reload_single_test_account

        reload_single_test_account()

    def test_concurrent_init_db_no_lock_errors(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_concurrent_init_", suffix=".db")[1])
        os.environ["FACTORY_DB_PATH"] = str(path)
        try:
            from factory.db import init_db

            errs: list[Exception] = []

            def _worker() -> None:
                try:
                    c = init_db(path)
                    c.close()
                except Exception as e:  # noqa: BLE001
                    errs.append(e)

            threads = [threading.Thread(target=_worker) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30.0)

            if errs:
                # Surface any sqlite "database is locked" style failures.
                raise AssertionError(f"init_db raised exceptions: {errs!r}")

            conn = sqlite3.connect(str(path), timeout=30.0)
            conn.row_factory = sqlite3.Row
            try:
                # Schema marker (все миграции включая improvement_candidates + file_changes.intent_override)
                mv = conn.execute("SELECT MAX(version) FROM migrations").fetchone()[0]
                self.assertGreaterEqual(int(mv), 3)

                # Tables exist
                for tname in ("agents", "api_accounts", "system_state", "state_transitions", "event_log"):
                    row = conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                        (tname,),
                    ).fetchone()
                    self.assertIsNotNone(row, f"missing table: {tname}")

                # Seed data
                n_acc = conn.execute("SELECT COUNT(*) AS c FROM api_accounts").fetchone()["c"]
                self.assertGreaterEqual(int(n_acc), 1)
                n_agents = conn.execute("SELECT COUNT(*) AS c FROM agents").fetchone()["c"]
                self.assertGreaterEqual(int(n_agents), 1)
                st = conn.execute(
                    "SELECT value FROM system_state WHERE key = 'active_account_id'"
                ).fetchone()
                self.assertIsNotNone(st)
            finally:
                conn.close()
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def test_concurrent_init_db_multi_process(self) -> None:
        """
        Smoke: два/три отдельных процесса одновременно вызывают init_db() на одной БД.
        Это ближе к реальному "2 CLI worker" сценарию, чем треды.
        """
        path = Path(tempfile.mkstemp(prefix="factory_concurrent_init_proc_", suffix=".db")[1])
        try:
            env = dict(os.environ)
            env["FACTORY_DB_PATH"] = str(path)
            env.setdefault("FACTORY_API_KEY_1", "ut-concurrent-init-proc")
            env.setdefault("FACTORY_API_NAME_1", "ut")
            env.setdefault("FACTORY_API_LIMIT_1", "3000")

            code = (
                "from pathlib import Path\n"
                "from factory.tests.qwen_cli_test_env import reload_single_test_account\n"
                "reload_single_test_account()\n"
                "from factory.db import init_db\n"
                "p = Path(r'''%s''')\n"
                "c = init_db(p)\n"
                "c.close()\n"
            ) % str(path)

            procs = [
                subprocess.Popen([sys.executable, "-c", code], env=env)
                for _ in range(3)
            ]
            for p in procs:
                p.wait(timeout=60.0)
            rc = [p.returncode for p in procs]
            self.assertTrue(all(r == 0 for r in rc), f"non-zero return codes: {rc}")
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()

