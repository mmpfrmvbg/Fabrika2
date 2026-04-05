from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from factory.composition import wire
from factory.db import gen_id, init_db
from factory.logging import FactoryLogger
from factory.models import Role, RunType, WorkItemStatus
from factory.queue_ops import claim_forge_inbox_atom
import factory.worker as worker_mod
from factory.worker import recover_stuck_running_work_items, worker_iteration


_FORGE_RUN_LOCK = threading.Lock()
_ITERATION_LOCK = threading.Lock()


def _open_db_with_retry(db_path: Path, attempts: int = 10) -> sqlite3.Connection:
    for _ in range(attempts):
        try:
            return init_db(db_path)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            time.sleep(0.1)
    raise AssertionError(f"unable to open sqlite db after retries: {db_path}")


@pytest.fixture(autouse=True)
def _serialize_forge_runs(monkeypatch: pytest.MonkeyPatch):
    original = worker_mod.forge.run_forge_queued_runs

    def _wrapped(orchestrator):
        with _FORGE_RUN_LOCK:
            return original(orchestrator)

    monkeypatch.setattr(worker_mod.forge, "run_forge_queued_runs", _wrapped)

def _seed_tree_with_atoms(conn: sqlite3.Connection, *, prefix: str, atom_count: int) -> list[str]:
    now = "2026-03-30T12:00:00.000000Z"
    vision_id = f"vis_{prefix}"
    epic_id = f"epi_{prefix}"
    story_id = f"sto_{prefix}"

    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority,
            created_at, updated_at
        )
        VALUES (?, NULL, ?, 'vision', 'Vision', '', 'planned',
                'planner', 'planner', 0, 100, ?, ?)
        """,
        (vision_id, vision_id, now, now),
    )
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority,
            created_at, updated_at
        )
        VALUES (?, ?, ?, 'epic', 'Epic', '', 'planned',
                'planner', 'planner', 1, 100, ?, ?)
        """,
        (epic_id, vision_id, vision_id, now, now),
    )
    conn.execute(
        """
        INSERT INTO work_items (
            id, parent_id, root_id, kind, title, description, status,
            creator_role, owner_role, planning_depth, priority,
            created_at, updated_at
        )
        VALUES (?, ?, ?, 'story', 'Story', '', 'planned',
                'planner', 'planner', 2, 100, ?, ?)
        """,
        (story_id, epic_id, vision_id, now, now),
    )

    atom_ids: list[str] = []
    for idx in range(atom_count):
        atom_id = f"atm_{prefix}_{idx:02d}"
        atom_ids.append(atom_id)
        fpath = f"factory/tests/stress_targets/{prefix}_{idx:02d}.txt"
        conn.execute(
            """
            INSERT INTO work_items (
                id, parent_id, root_id, kind, title, description, status,
                creator_role, owner_role, planning_depth, priority,
                created_at, updated_at
            )
            VALUES (?, ?, ?, 'atom', 'Atom', '', 'ready_for_work',
                    'planner', 'forge', 3, 100, ?, ?)
            """,
            (atom_id, story_id, vision_id, now, now),
        )
        conn.execute(
            """
            INSERT INTO work_item_files (id, work_item_id, path, intent, description, required)
            VALUES (?, ?, ?, 'modify', '', 1)
            """,
            (gen_id("wif"), atom_id, fpath),
        )
        conn.execute(
            """
            INSERT INTO work_item_queue (work_item_id, queue_name, priority, available_at)
            VALUES (?, 'forge_inbox', 10, ?)
            """,
            (atom_id, now),
        )

    conn.commit()
    return atom_ids


def _spawn_worker_threads(
    db_path: Path,
    worker_ids: list[str],
    stop_events: dict[str, threading.Event],
    errors: list[BaseException],
) -> list[threading.Thread]:
    threads: list[threading.Thread] = []

    def _runner(worker_id: str, stop_event: threading.Event) -> None:
        f = None
        try:
            for _ in range(8):
                try:
                    f = wire(db_path)
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower():
                        raise
                    time.sleep(0.1)
            if f is None:
                raise AssertionError(f"failed to open worker connection for {worker_id}")
            deadline = time.monotonic() + 120.0
            while time.monotonic() < deadline and not stop_event.is_set():
                try:
                    with _ITERATION_LOCK:
                        worked = worker_iteration(f, worker_id)
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower():
                        raise
                    time.sleep(0.02)
                    continue
                if not worked:
                    time.sleep(0.02)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            if f is not None:
                f["conn"].close()

    for worker_id in worker_ids:
        ev = stop_events.setdefault(worker_id, threading.Event())
        t = threading.Thread(target=_runner, args=(worker_id, ev), daemon=True)
        threads.append(t)
        t.start()

    return threads


def _wait_until_all_done(conn: sqlite3.Connection, atom_ids: list[str], timeout_sec: float = 90.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM work_items
                WHERE kind = 'atom' AND id IN ({}) AND status = ?
                """.format(",".join("?" * len(atom_ids))),
                [*atom_ids, WorkItemStatus.DONE.value],
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            time.sleep(0.05)
            continue
        if int(row["c"]) == len(atom_ids):
            return
        time.sleep(0.05)
    raise AssertionError("timeout waiting for all atoms to reach done")


def _assert_concurrency_invariants(conn: sqlite3.Connection, atom_ids: list[str]) -> None:
    placeholders = ",".join("?" * len(atom_ids))

    dup_done_runs = conn.execute(
        f"""
        SELECT work_item_id, COUNT(*) AS c
        FROM runs
        WHERE role = ? AND run_type = ? AND status = 'completed'
          AND work_item_id IN ({placeholders})
        GROUP BY work_item_id
        HAVING COUNT(*) > 1
        """,
        [Role.FORGE.value, RunType.IMPLEMENT.value, *atom_ids],
    ).fetchall()
    assert dup_done_runs == []

    no_completed = conn.execute(
        f"""
        SELECT work_item_id
        FROM runs
        WHERE role = ? AND run_type = ? AND status = 'completed'
          AND work_item_id IN ({placeholders})
        """,
        [Role.FORGE.value, RunType.IMPLEMENT.value, *atom_ids],
    ).fetchall()
    assert len(no_completed) == len(atom_ids)

    active_lease_for_done = conn.execute(
        f"""
        SELECT wi.id
        FROM work_items wi
        LEFT JOIN work_item_queue wiq ON wiq.work_item_id = wi.id
        WHERE wi.id IN ({placeholders})
          AND wi.status = 'done'
          AND wiq.lease_owner IS NOT NULL
        """,
        atom_ids,
    ).fetchall()
    assert active_lease_for_done == []

    orphan_queue_for_done = conn.execute(
        f"""
        SELECT wi.id
        FROM work_items wi
        LEFT JOIN work_item_queue wiq ON wiq.work_item_id = wi.id
        WHERE wi.id IN ({placeholders})
          AND wi.status = 'done'
          AND wiq.work_item_id IS NOT NULL
        """,
        atom_ids,
    ).fetchall()
    assert orphan_queue_for_done == []


def _recover_and_drain(db_path: Path, atom_ids: list[str], timeout_sec: float = 90.0) -> None:
    f = None
    for _ in range(10):
        try:
            f = wire(db_path)
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            time.sleep(0.1)
    if f is None:
        return
    try:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            row = f["conn"].execute(
                "SELECT COUNT(*) AS c FROM work_items WHERE kind='atom' AND id IN ({}) AND status = ?".format(
                    ",".join("?" * len(atom_ids))
                ),
                [*atom_ids, WorkItemStatus.DONE.value],
            ).fetchone()
            if int(row["c"]) == len(atom_ids):
                return
            recover_stuck_running_work_items(
                f["conn"],
                f["logger"],
                worker_id="post-stress-recover",
            )
            f["orchestrator"]._expire_leases()
            f["conn"].commit()
            worker_iteration(f, "post-stress-drain")
            f["conn"].commit()
            time.sleep(0.03)
    finally:
        f["conn"].close()


@pytest.fixture
def stress_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "concurrency_stress.db"
    monkeypatch.setenv("FACTORY_DB_PATH", str(db_path))
    monkeypatch.setenv("FACTORY_QWEN_DRY_RUN", "1")
    monkeypatch.setenv("FACTORY_ORCHESTRATOR_ASYNC", "0")
    return db_path


def test_many_workers_many_atoms_no_duplicate_runs(stress_db: Path) -> None:
    conn = _open_db_with_retry(stress_db)
    atom_ids = _seed_tree_with_atoms(conn, prefix="many", atom_count=16)
    conn.close()

    errors: list[BaseException] = []
    stop_events: dict[str, threading.Event] = {}
    threads = _spawn_worker_threads(
        stress_db,
        ["stress-w1", "stress-w2", "stress-w3", "stress-w4"],
        stop_events,
        errors,
    )

    monitor = _open_db_with_retry(stress_db)
    try:
        _wait_until_all_done(monitor, atom_ids)
    except AssertionError:
        _recover_and_drain(stress_db, atom_ids, timeout_sec=120.0)
        _wait_until_all_done(monitor, atom_ids, timeout_sec=30.0)
    finally:
        for ev in stop_events.values():
            ev.set()
        for thread in threads:
            thread.join(timeout=5.0)

    assert errors == []
    _assert_concurrency_invariants(monitor, atom_ids)
    monitor.close()


def test_restart_recovery_race_under_load(stress_db: Path) -> None:
    conn = _open_db_with_retry(stress_db)
    atom_ids = _seed_tree_with_atoms(conn, prefix="recover", atom_count=6)
    conn.close()

    errors: list[BaseException] = []
    stop_events: dict[str, threading.Event] = {}
    worker_threads = _spawn_worker_threads(
        stress_db,
        ["steady-worker"],
        stop_events,
        errors,
    )

    crashed_item: str | None = None
    crash_factory = wire(stress_db)
    try:
        crashed_item = claim_forge_inbox_atom(crash_factory["conn"], "dead-worker")
        assert crashed_item is not None
        crash_factory["conn"].execute(
            """
            UPDATE work_items
            SET status = 'running',
                previous_status = 'ready_for_work',
                last_heartbeat_at = NULL
            WHERE id = ?
            """,
            (crashed_item,),
        )
        crash_factory["conn"].commit()
    finally:
        crash_factory["conn"].close()

    rec_conn = _open_db_with_retry(stress_db)
    recovered = recover_stuck_running_work_items(
        rec_conn,
        FactoryLogger(rec_conn),
        worker_id="recovery-pass",
    )
    rec_conn.commit()
    assert recovered >= 1

    if crashed_item is not None:
        row = rec_conn.execute(
            "SELECT status FROM work_items WHERE id = ?",
            (crashed_item,),
        ).fetchone()
        assert row is not None
        assert row["status"] == WorkItemStatus.READY_FOR_WORK.value

    replacement_threads = _spawn_worker_threads(
        stress_db,
        ["replacement-a", "replacement-b"],
        stop_events,
        errors,
    )

    try:
        _wait_until_all_done(rec_conn, atom_ids)
    except AssertionError:
        _recover_and_drain(stress_db, atom_ids, timeout_sec=60.0)
        _wait_until_all_done(rec_conn, atom_ids, timeout_sec=20.0)
    finally:
        for ev in stop_events.values():
            ev.set()
        for thread in worker_threads + replacement_threads:
            thread.join(timeout=5.0)

    assert errors == []
    stuck = rec_conn.execute(
        "SELECT id FROM work_items WHERE id IN ({}) AND status = 'running'".format(",".join("?" * len(atom_ids))),
        atom_ids,
    ).fetchall()
    assert stuck == []

    leased_dead = rec_conn.execute(
        "SELECT work_item_id FROM work_item_queue WHERE lease_owner = 'dead-worker'"
    ).fetchall()
    assert leased_dead == []

    _assert_concurrency_invariants(rec_conn, atom_ids)
    rec_conn.close()


def test_lease_expiry_race_reclaims_without_duplicates(stress_db: Path) -> None:
    conn = _open_db_with_retry(stress_db)
    atom_ids = _seed_tree_with_atoms(conn, prefix="lease", atom_count=6)
    conn.close()

    errors: list[BaseException] = []
    stop_events: dict[str, threading.Event] = {}
    threads = _spawn_worker_threads(stress_db, ["lease-a", "lease-b", "lease-c"], stop_events, errors)

    orch_factory = wire(stress_db)
    monitor = _open_db_with_retry(stress_db)
    try:
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            leased = monitor.execute(
                "SELECT COUNT(*) AS c FROM work_item_queue WHERE lease_owner IS NOT NULL"
            ).fetchone()["c"]
            if int(leased) > 0:
                break
            time.sleep(0.03)

        orch_factory["conn"].execute(
            """
            UPDATE work_item_queue
            SET lease_until = strftime('%Y-%m-%dT%H:%M:%f', 'now', '-10 minutes')
            WHERE lease_owner IS NOT NULL
            """
        )
        orch_factory["conn"].commit()
        orch_factory["orchestrator"]._expire_leases()
        orch_factory["conn"].commit()

        stale = orch_factory["conn"].execute(
            """
            SELECT work_item_id FROM work_item_queue
            WHERE lease_until IS NOT NULL
              AND lease_until < strftime('%Y-%m-%dT%H:%M:%f','now')
            """
        ).fetchall()
        assert stale == []

        try:
            _wait_until_all_done(monitor, atom_ids)
        except AssertionError:
            _recover_and_drain(stress_db, atom_ids, timeout_sec=60.0)
            _wait_until_all_done(monitor, atom_ids, timeout_sec=20.0)
        _assert_concurrency_invariants(monitor, atom_ids)
    finally:
        for ev in stop_events.values():
            ev.set()
        for thread in threads:
            thread.join(timeout=5.0)
        orch_factory["conn"].close()
        monitor.close()

    assert errors == []


def test_transient_sqlite_lock_contention_preserves_invariants(stress_db: Path) -> None:
    conn = _open_db_with_retry(stress_db)
    atom_ids = _seed_tree_with_atoms(conn, prefix="lock", atom_count=6)
    conn.close()

    errors: list[BaseException] = []
    stop_events: dict[str, threading.Event] = {}
    workers = _spawn_worker_threads(
        stress_db,
        ["lock-w1", "lock-w2", "lock-w3"],
        stop_events,
        errors,
    )

    lock_stop = threading.Event()
    lock_errors: list[BaseException] = []

    def _locker() -> None:
        c2 = sqlite3.connect(str(stress_db), timeout=5.0, check_same_thread=False)
        c2.row_factory = sqlite3.Row
        c2.execute("PRAGMA journal_mode = WAL")
        c2.execute("PRAGMA busy_timeout = 30000")
        try:
            end_at = time.monotonic() + 2.5
            while time.monotonic() < end_at and not lock_stop.is_set():
                c2.execute("BEGIN IMMEDIATE")
                c2.execute(
                    "UPDATE work_item_queue SET available_at = available_at WHERE queue_name = 'forge_inbox'"
                )
                time.sleep(0.03)
                c2.commit()
                time.sleep(0.02)
        except BaseException as exc:  # noqa: BLE001
            lock_errors.append(exc)
            try:
                c2.rollback()
            except Exception:
                pass
        finally:
            c2.close()

    locker_thread = threading.Thread(target=_locker, daemon=True)
    locker_thread.start()

    monitor = _open_db_with_retry(stress_db)
    try:
        try:
            _wait_until_all_done(monitor, atom_ids)
        except AssertionError:
            _recover_and_drain(stress_db, atom_ids, timeout_sec=60.0)
            _wait_until_all_done(monitor, atom_ids, timeout_sec=20.0)
    finally:
        lock_stop.set()
        locker_thread.join(timeout=5.0)
        for ev in stop_events.values():
            ev.set()
        for thread in workers:
            thread.join(timeout=5.0)

    assert errors == []
    assert all("locked" in str(exc).lower() for exc in lock_errors)

    _assert_concurrency_invariants(monitor, atom_ids)

    bad_leases = monitor.execute(
        """
        SELECT wiq.work_item_id
        FROM work_item_queue wiq
        LEFT JOIN work_items wi ON wi.id = wiq.work_item_id
        WHERE wiq.lease_owner IS NOT NULL
          AND (wi.id IS NULL OR wi.status != 'ready_for_work')
        """
    ).fetchall()
    assert bad_leases == []
    monitor.close()
