from __future__ import annotations

from fastapi.testclient import TestClient

from factory.api_server import app
from factory.db import init_db


def test_judge_verdicts_endpoints_return_list_when_empty(monkeypatch, tmp_path) -> None:
    db = tmp_path / "judge_verdicts_empty.db"
    monkeypatch.setenv("FACTORY_DB", str(db))
    init_db(db).close()

    client = TestClient(app)

    for path in ("/api/verdicts", "/api/judge_verdicts"):
        response = client.get(path)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert isinstance(payload, list)
        assert payload == []

    wrapped = client.get("/api/judgements")
    assert wrapped.status_code == 200, wrapped.text
    wrapped_payload = wrapped.json()
    assert isinstance(wrapped_payload, dict)
    assert isinstance(wrapped_payload.get("items"), list)
    assert wrapped_payload.get("items") == []


def test_dashboard_list_endpoints_never_return_null_arrays(monkeypatch, tmp_path) -> None:
    db = tmp_path / "dashboard_lists.db"
    monkeypatch.setenv("FACTORY_DB", str(db))
    init_db(db).close()

    client = TestClient(app)

    failures = client.get("/api/failures")
    assert failures.status_code == 200, failures.text
    failures_payload = failures.json()
    assert isinstance(failures_payload.get("items"), list)
    assert isinstance(failures_payload.get("clusters"), list)

    improvements = client.get("/api/improvements")
    assert improvements.status_code == 200, improvements.text
    improvements_payload = improvements.json()
    assert isinstance(improvements_payload.get("candidates"), list)
    assert isinstance(improvements_payload.get("stats"), dict)
