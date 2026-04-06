from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest

from factory import webhooks


@pytest.mark.asyncio
async def test_send_webhook_posts_json_with_signature(monkeypatch) -> None:
    url = "http://example.test/factory-hook"
    monkeypatch.setattr(webhooks, "FACTORY_WEBHOOK_URL", url)
    monkeypatch.setattr(webhooks, "FACTORY_WEBHOOK_SECRET", "super-secret")

    client = AsyncMock()
    client.post = AsyncMock(return_value=None)
    cm = AsyncMock()
    cm.__aenter__.return_value = client
    cm.__aexit__.return_value = False

    payload = webhooks.build_payload(
        event_type="work_item.completed",
        work_item_id="wi-123",
        title="Ship feature",
        status="done",
    )

    with patch("factory.webhooks.httpx.AsyncClient", return_value=cm) as async_client_cls:
        await webhooks.send_webhook(payload)

    async_client_cls.assert_called_once_with(timeout=10.0)
    client.post.assert_awaited_once()
    call = client.post.await_args
    assert call.args[0] == url

    raw_body = call.kwargs["content"].decode("utf-8")
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
    assert call.kwargs["headers"]["X-Webhook-Signature"] == expected_sig


@pytest.mark.asyncio
async def test_send_webhook_network_error(monkeypatch) -> None:
    url = "http://example.test/factory-hook"
    monkeypatch.setattr(webhooks, "FACTORY_WEBHOOK_URL", url)
    monkeypatch.setattr(webhooks, "FACTORY_WEBHOOK_SECRET", "super-secret")

    client = AsyncMock()
    client.post = AsyncMock(side_effect=webhooks.httpx.TransportError("boom"))
    cm = AsyncMock()
    cm.__aenter__.return_value = client
    cm.__aexit__.return_value = False

    payload = webhooks.build_payload(
        event_type="work_item.failed",
        work_item_id="wi-err",
        title="Broken",
        status="failed",
    )

    with patch("factory.webhooks.httpx.AsyncClient", return_value=cm):
        with pytest.raises(webhooks.httpx.TransportError):
            await webhooks.send_webhook(payload)


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

    webhooks.notify_state_change(
        event_name="forge_failed",
        work_item_id="wi-4",
        title="Dead letter",
        status="dead",
    )
    assert len(sent) == 4
    assert sent[3]["event_type"] == "work_item.dead"
