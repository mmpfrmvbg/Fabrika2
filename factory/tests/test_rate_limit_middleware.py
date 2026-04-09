import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
import os


@pytest.fixture(scope="module")
def client():
    os.environ.setdefault("FACTORY_API_KEY", "test-key")
    from factory.api_server import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_normal_request_not_rate_limited(client):
    resp = client.get("/health", headers={"X-API-Key": "test-key"})
    assert resp.status_code != 429


def test_rate_limit_429_response(client):
    from factory import middleware as m
    meta = {"is_limited": True, "retry_after": 30, "limit": 60, "remaining": 0}
    with patch.object(m, "_rate_limit_meta", return_value=meta):
        resp = client.get("/health", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 429
    assert resp.json().get("detail") == "Rate limit exceeded"
    assert resp.headers.get("Retry-After") == "30"


def test_rate_limit_headers_on_success(client):
    from factory import middleware as m
    meta = {"is_limited": False, "retry_after": 0, "limit": 60, "remaining": 55}
    with patch.object(m, "_rate_limit_meta", return_value=meta):
        resp = client.get("/health", headers={"X-API-Key": "test-key"})
    assert "X-RateLimit-Limit" in resp.headers
    assert resp.headers["X-RateLimit-Remaining"] == "55"
