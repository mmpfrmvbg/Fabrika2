"""
Машиночитаемый контракт для записей единого журнала (read-model поверх event_log + merge).

Версия схемы ответа ``GET /api/journal`` — :data:`JOURNAL_SCHEMA_VERSION`.

Запись в ``event_log`` использует ``EventType`` из ``factory.models`` (строковые значения
``event_type``); при записи ``payload`` всегда сериализуется как JSON-объект (см. ``FactoryLogger``).
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

JOURNAL_SCHEMA_VERSION = 1


class EventLogRecordBase(BaseModel):
    """Базовая форма строки ``event_log`` (логическая; фактически — таблица SQLite)."""

    model_config = ConfigDict(extra="forbid")

    id: Optional[int] = None
    created_at: str = Field("", description="event_time ISO")
    event_type: str = ""
    work_item_id: Optional[str] = None
    run_id: Optional[str] = None
    actor: Optional[str] = Field(None, description="actor_role")
    payload: dict[str, Any] = Field(default_factory=dict)


class NormalizedJournalEvent(BaseModel):
    """Стабильное представление одной строки unified journal после нормализации."""

    model_config = ConfigDict(extra="forbid")

    ts: str
    actor: Optional[str] = None
    event_type: str = Field(
        ...,
        description="kind/title из merge (например judge_verdict, run_started)",
    )
    short_message: str = ""
    source_type: str = "unknown"
    source_id: str = ""
    work_item_id: Optional[str] = None
    run_id: Optional[str] = None
    normalized_payload: dict[str, Any] = Field(default_factory=dict)
    degraded: bool = False
    degrade_reason: Optional[str] = None


def _payload_as_dict(p: Any) -> dict[str, Any]:
    if p is None:
        return {}
    if isinstance(p, dict):
        return dict(p)
    return {"message": str(p)}


def enrich_journal_item(item: dict[str, Any]) -> dict[str, Any]:
    """
    Добавляет к элементу unified journal поля ``schema_version`` и ``normalized``
    (без удаления исходных ключей). При ошибке — degraded.
    """
    out = dict(item)
    out["schema_version"] = JOURNAL_SCHEMA_VERSION
    try:
        n = NormalizedJournalEvent(
            ts=str(item.get("ts") or ""),
            actor=item.get("role"),
            event_type=str(item.get("kind") or item.get("title") or "event"),
            short_message=str(item.get("summary") or "")[:2000],
            source_type=str(item.get("source_type") or "unknown"),
            source_id=str(item.get("source_id") or item.get("source_key") or ""),
            work_item_id=item.get("work_item_id"),
            run_id=item.get("run_id"),
            normalized_payload=_payload_as_dict(item.get("payload")),
            degraded=False,
        )
        out["normalized"] = n.model_dump()
    except (ValidationError, TypeError, ValueError) as e:
        out["normalized"] = {
            "degraded": True,
            "degrade_reason": str(e),
            "ts": str(item.get("ts") or ""),
            "event_type": str(item.get("kind") or "unknown"),
            "short_message": str(item.get("summary") or "")[:500],
        }
    return out


def enrich_journal_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [enrich_journal_item(it) for it in items]
