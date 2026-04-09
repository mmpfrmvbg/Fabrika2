from factory.models import EventType
from factory.orchestrator_thread import _OrchestratorThread


def test_orchestrator_thread_start_stop(monkeypatch):
    created_threads: list[FakeThread] = []

    class FakeThread:
        def __init__(self, *, target, daemon, name):
            self.target = target
            self.daemon = daemon
            self.name = name
            self.started = False
            self.joined = False
            self._alive = False
            created_threads.append(self)

        def start(self):
            self.started = True
            self._alive = True

        def join(self, timeout=None):
            self.joined = True
            self._alive = False

        def is_alive(self):
            return self._alive

    monkeypatch.setattr("factory.orchestrator_thread.threading.Thread", FakeThread)

    t = _OrchestratorThread()
    t.start()
    assert t.running is True
    assert len(created_threads) == 1
    assert created_threads[0].started is True

    t.start()
    assert len(created_threads) == 1

    t.stop()
    assert t.running is False
    assert created_threads[0].joined is True


def test_orchestrator_thread_tick_once_counts_started_events():
    class FakeCursor:
        def __init__(self, one=None, all_rows=None):
            self._one = one
            self._all_rows = all_rows or []

        def fetchone(self):
            return self._one

        def fetchall(self):
            return self._all_rows

    class FakeConn:
        def __init__(self):
            self.closed = False
            self.queries = []

        def execute(self, query, params=None):
            self.queries.append((query, params))
            if "COALESCE(MAX(id), 0)" in query:
                return FakeCursor(one={"m": 10})
            return FakeCursor(
                all_rows=[
                    {"event_type": EventType.FORGE_STARTED.value},
                    {"event_type": EventType.FORGE_STARTED.value},
                    {"event_type": EventType.REVIEW_STARTED.value},
                    {"event_type": "irrelevant"},
                ]
            )

        def close(self):
            self.closed = True

    class FakeOrchestrator:
        def __init__(self):
            self.ticked = 0

        def tick(self):
            self.ticked += 1

    conn = FakeConn()
    orch = FakeOrchestrator()
    t = _OrchestratorThread()

    result = t.tick_once(_factory={"conn": conn, "orchestrator": orch})

    assert result == {"forge": 2, "review": 1}
    assert t.ticks_total == 1
    assert t.last_tick is not None
    assert t.last_tick_processed == {"forge": 2, "review": 1, "judge": 0}
    assert orch.ticked == 1
    assert conn.closed is False
