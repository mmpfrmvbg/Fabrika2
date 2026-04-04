from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import pytest

from factory import webhooks

httpretty = pytest.importorskip("httpretty")


@httpretty.activate(allow_net_connect=False)
def test_send_webhook_posts_json_with_signature(monkeypatch) -> None:
    url = "http://example.test/factory-hook"
    monkeypatch.setattr(webhooks, "FACTORY_WEBHOOK_URL", url)
    monkeypatch.setattr(webhooks, "FACTORY_WEBHOOK_SECRET", "super-secret")

    httpretty.register_uri(httpretty.POST, url, body="ok", status=200)

    payload = webhooks.build_payload(
        event_type="work_item.completed",
        work_item_id="wi-123",
        title="Ship feature",
        status="done",
    )
    asyncio.run(webhooks.send_webhook(payload))

    req = httpretty.last_request()
    assert req.method == "POST"
    assert req.path == "/factory-hook"

    raw_body = req.body.decode("utf-8") if isinstance(req.body, bytes) else req.body
    data = json.loads(raw_body)
    assert data["event_type"] == "work_item.completed"
    assert data["work_item_id"] == "wi-123"
    assert data["title"] == "Ship feature"
    assert data["status"] == "done"
    assert "timestamp" in data

    expected_sig = "sha256=" + hmac.new(
        b"super-secret",
        raw_body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert req.headers["X-Webhook-Signature"] == expected_sig


def test_notify_state_change_emits_expected_payload(monkeypatch) -> None:
    sent: list[dict[str, str]] = []
    monkeypatch.setattr(webhooks, "send_webhook_async", lambda payload: sent.append(payload))

    webhooks.notify_state_change(
        event_name="forge_completed",
        work_item_id="wi-1",
        title="Implement API",
        status="done",
    )
    assert len(sent) == 1
    assert sent[0]["event_type"] == "work_item.completed"
    assert sent[0]["work_item_id"] == "wi-1"

    webhooks.notify_state_change(
        event_name="forge_failed",
        work_item_id="wi-2",
        title="Broken",
        status="review_rejected",
    )
    assert len(sent) == 2
    assert sent[1]["event_type"] == "work_item.failed"

    webhooks.notify_state_change(
        event_name="dependency_unmet",
        work_item_id="wi-3",
        title="Blocked",
        status="blocked",
    )
    assert len(sent) == 3
    assert sent[2]["event_type"] == "work_item.stuck"
