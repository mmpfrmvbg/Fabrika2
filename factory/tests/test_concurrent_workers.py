"""Два worker-потока на одной БД: нет двойных forge runs, все атомы доходят до done (DRY_RUN)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from factory.composition import wire
from factory.db import gen_id, init_db
from factory.models import Role, RunType, WorkItemStatus
from factory.worker import worker_iteration


class ConcurrentWorkersTests(unittest.TestCase):
    def test_two_worker_threads_four_atoms_all_done(self) -> None:
        path = Path(tempfile.mkstemp(prefix="factory_conc_workers_", suffix=".db")[1])
        prev_db = os.environ.get("FACTORY_DB_PATH")
        prev_dry = os.environ.get("FACTORY_QWEN_DRY_RUN")
        prev_async = os.environ.get("FACTORY_ORCHESTRATOR_ASYNC")
        try:
            os.environ["FACTORY_QWEN_DRY_RUN"] = "1"
            os.environ["FACTORY_ORCHESTRATOR_ASYNC"] = "0"
            os.environ["FACTORY_DB_PATH"] = str(path)

            conn = init_db(path)
            now = "2026-03-30T12:00:00.000000Z"
            vid = "vis_cw"
            eid = "epi_cw"
            sid = "sto_cw"
            atoms = ["atm_cw_1", "atm_cw_2", "atm_cw_3", "atm_cw_4"]
            files = [
                "factory/__init__.py",
                "factory/cli.py",
                "factory/config.py",
                "factory/db.py",
            ]

            conn.execute(
                """
                INSERT INTO work_items (
                    id, parent_id, root_id, kind, title, description, status,
                    creator_role, owner_role, planning_depth, priority,
                    created_at, updated_at
                )
                VALUES (?, NULL, ?, 'vision', 'V', '', 'planned',
                        'planner', 'planner', 0, 100, ?, ?)
                """,
                (vid, vid, now, now),
            )
            conn.execute(
                """
                INSERT INTO work_items (
                    id, parent_id, root_id, kind, title, description, status,
                    creator_role, owner_role, planning_depth, priority,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, 'epic', 'E', '', 'planned',
                        'planner', 'planner', 1, 100, ?, ?)
                """,
                (eid, vid, vid, now, now),
            )
            conn.execute(
                """
                INSERT INTO work_items (
                    id, parent_id, root_id, kind, title, description, status,
                    creator_role, owner_role, planning_depth, priority,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, 'story', 'S', '', 'planned',
                        'planner', 'planner', 2, 100, ?, ?)
                """,
                (sid, eid, vid, now, now),
            )
            for aid, fpath in zip(atoms, files, strict=True):
                conn.execute(
                    """
                    INSERT INTO work_items (
                        id, parent_id, root_id, kind, title, description, status,
                        creator_role, owner_role, planning_depth, priority,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, 'atom', 'A', '', 'ready_for_work',
                            'planner', 'forge', 3, 100, ?, ?)
                    """,
                    (aid, sid, vid, now, now),
                )
                conn.execute(
                    """
                    INSERT INTO work_item_files (id, work_item_id, path, intent, description, required)
                    VALUES (?, ?, ?, 'modify', '', 1)
                    """,
                    (gen_id("wif"), aid, fpath),
                )
                conn.execute(
                    """
                    INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at)
                    VALUES (?, 'forge_inbox', 10, ?)
                    """,
                    (aid, now),
                )
            conn.commit()
            conn.close()

            stop = threading.Event()
            err: list[BaseException] = []

            def run_w(wid: str) -> None:
                try:
                    for _ in range(5):
                        try:
                            f = wire(path)
                            break
                        except sqlite3.OperationalError as e:
                            if "locked" not in str(e).lower():
                                raise
                            time.sleep(0.1)
                    else:
                        raise AssertionError(f"failed to open worker connection for {wid}")

                    bt = int(f["conn"].execute("PRAGMA busy_timeout").fetchone()[0])
                    jm = str(f["conn"].execute("PRAGMA journal_mode").fetchone()[0]).lower()
                    if bt < 30000:
                        raise AssertionError(f"busy_timeout too low on {wid}: {bt}")
                    if jm != "wal":
                        raise AssertionError(f"journal_mode is not WAL on {wid}: {jm}")
                    deadline = time.monotonic() + 120.0
                    while time.monotonic() < deadline and not stop.is_set():
                        try:
                            if worker_iteration(f, wid):
                                continue
                        except sqlite3.OperationalError as e:
                            if "locked" not in str(e).lower():
                                raise
                            time.sleep(0.05)
                            continue
                        time.sleep(0.05)
                        try:
                            done_n = f["conn"].execute(
                                """
                                SELECT COUNT(*) AS c FROM work_items
                                WHERE kind = 'atom' AND status = ?
                                """,
                                (WorkItemStatus.DONE.value,),
                            ).fetchone()["c"]
                        except sqlite3.OperationalError as e:
                            if "locked" not in str(e).lower():
                                raise
                            time.sleep(0.05)
                            continue
                        if int(done_n) >= len(atoms):
                            break
                    f["conn"].close()
                except BaseException as e:
                    err.append(e)

            t1 = threading.Thread(target=run_w, args=("worker-a",), daemon=True)
            t2 = threading.Thread(target=run_w, args=("worker-b",), daemon=True)
            t1.start()
            t2.start()
            t1.join(timeout=125.0)
            t2.join(timeout=125.0)
            stop.set()

            self.assertEqual(err, [])

            conn = init_db(path)
            for aid in atoms:
                st = conn.execute(
                    "SELECT status FROM work_items WHERE id = ?",
                    (aid,),
                ).fetchone()["status"]
                self.assertEqual(st, WorkItemStatus.DONE.value, msg=f"atom {aid} not done")

            for aid in atoms:
                n = conn.execute(
                    """
                    SELECT COUNT(*) AS c FROM runs
                    WHERE work_item_id = ? AND role = ? AND run_type = ? AND status = 'completed'
                    """,
                    (aid, Role.FORGE.value, RunType.IMPLEMENT.value),
                ).fetchone()["c"]
                self.assertEqual(int(n), 1, msg=f"expected 1 completed forge run for {aid}")
            conn.close()
        finally:
            if prev_db is None:
                os.environ.pop("FACTORY_DB_PATH", None)
            else:
                os.environ["FACTORY_DB_PATH"] = prev_db
            if prev_dry is None:
                os.environ.pop("FACTORY_QWEN_DRY_RUN", None)
            else:
                os.environ["FACTORY_QWEN_DRY_RUN"] = prev_dry
            if prev_async is None:
                os.environ.pop("FACTORY_ORCHESTRATOR_ASYNC", None)
            else:
                os.environ["FACTORY_ORCHESTRATOR_ASYNC"] = prev_async
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
