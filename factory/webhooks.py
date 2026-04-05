"""Outgoing webhook notifications for important work-item state changes."""

from __future__ import annotations

import asyncio
import hmac
import json
import threading
from datetime import datetime, timezone
from hashlib import sha256

import httpx

from .config import FACTORY_WEBHOOK_SECRET, FACTORY_WEBHOOK_URL

_EVENT_HEADER = "X-Webhook-Signature"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_payload(*, event_type: str, work_item_id: str, title: str | None, status: str) -> dict[str, str]:
    return {
        "event_type": event_type,
        "work_item_id": work_item_id,
        "title": title or "",
        "status": status,
        "timestamp": _utc_now_iso(),
    }


def _build_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, sha256).hexdigest()
    return f"sha256={digest}"


async def send_webhook(payload: dict[str, str]) -> None:
    url = (FACTORY_WEBHOOK_URL or "").strip()
    if not url:
        return

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    secret = (FACTORY_WEBHOOK_SECRET or "").strip()
    if secret:
        headers[_EVENT_HEADER] = _build_signature(secret, body)

    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(url, content=body, headers=headers)


def send_webhook_async(payload: dict[str, str]) -> None:
    """Fire-and-forget sender that never blocks FSM transitions."""

    def _runner() -> None:
        try:
            asyncio.run(send_webhook(payload))
        except Exception:
            return

    threading.Thread(target=_runner, daemon=True).start()


def event_type_for_state_change(*, event_name: str, new_status: str) -> str | None:
    if new_status == "dead":
        return "work_item.dead"
    if new_status == "done":
        return "work_item.completed"
    if new_status == "blocked":
        return "work_item.stuck"
    if event_name in {"forge_failed", "review_failed"}:
        return "work_item.failed"
    return None


def notify_state_change(*, event_name: str, work_item_id: str, title: str | None, status: str) -> None:
    event_type = event_type_for_state_change(event_name=event_name, new_status=status)
    if not event_type:
        return
    payload = build_payload(
        event_type=event_type,
        work_item_id=work_item_id,
        title=title,
        status=status,
    )
    send_webhook_async(payload)


def notify_stuck(*, work_item_id: str, title: str | None, status: str) -> None:
    payload = build_payload(
        event_type="work_item.stuck",
        work_item_id=work_item_id,
        title=title,
        status=status,
    )
    send_webhook_async(payload)


def notify_event(*, event_type: str, work_item_id: str, title: str | None, status: str) -> None:
    payload = build_payload(
        event_type=event_type,
        work_item_id=work_item_id,
        title=title,
        status=status,
    )
    send_webhook_async(payload)
