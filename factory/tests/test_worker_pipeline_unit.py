from __future__ import annotations

import sqlite3

from factory.worker_pipeline import drain_atom_downstream


class _FakeOrchestrator:
    def __init__(self, conn: sqlite3.Connection, calls: list[str]) -> None:
        self.conn = conn
        self.calls = calls

    def _expire_leases(self) -> None:
        self.calls.append("expire")

    def process_review_queue(self) -> None:
        self.calls.append("review")

    def _dispatch_judge(self, _item: dict) -> None:
        self.calls.append("dispatch_judge")

    def _process_queue(self, _queue, _handler) -> None:
        self.calls.append("judge")
        self.conn.execute("UPDATE work_items SET status = 'done' WHERE id = 'wi1'")

    def _escalate_review_rejected_to_judge(self) -> None:
        self.calls.append("escalate")


def _make_conn(status: str = "in_progress") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE work_items (id TEXT PRIMARY KEY, status TEXT NOT NULL)")
    conn.execute("INSERT INTO work_items(id, status) VALUES ('wi1', ?)", (status,))
    conn.commit()
    return conn


def test_drain_atom_downstream_runs_pipeline_stages_in_sequence(monkeypatch) -> None:
    conn = _make_conn("in_progress")
    calls: list[str] = []
    orch = _FakeOrchestrator(conn, calls)

    def _forge(_orch) -> None:
        calls.append("forge")

    monkeypatch.setattr("factory.worker_pipeline.forge.run_forge_queued_runs", _forge)

    drain_atom_downstream(orch, "wi1", max_rounds=3)

    assert calls == ["expire", "forge", "review", "judge", "escalate"]


def test_drain_atom_downstream_handles_failure_state_without_exception(monkeypatch) -> None:
    conn = _make_conn("in_progress")
    calls: list[str] = []
    orch = _FakeOrchestrator(conn, calls)

    def _forge(_orch) -> None:
        calls.append("forge")

    def _judge_queue(_queue, _handler) -> None:
        calls.append("judge")
        conn.execute("UPDATE work_items SET status = 'ready_for_work' WHERE id = 'wi1'")

    monkeypatch.setattr("factory.worker_pipeline.forge.run_forge_queued_runs", _forge)
    monkeypatch.setattr(orch, "_process_queue", _judge_queue)

    drain_atom_downstream(orch, "wi1", max_rounds=3)

    status = conn.execute("SELECT status FROM work_items WHERE id = 'wi1'").fetchone()["status"]
    assert status == "ready_for_work"
    assert calls == ["expire", "forge", "review", "judge", "escalate"]
