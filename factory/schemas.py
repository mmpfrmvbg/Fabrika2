from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class WorkItemPatchRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10000)


class BulkArchiveRequest(BaseModel):
    ids: list[str] | None = None
    filter: str | None = None


class ImprovementReviewRequest(BaseModel):
    reviewed_by: str | None = Field(default="dashboard", min_length=1, max_length=128)


class VisionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10000)


class WorkItemCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    kind: str = Field(default="vision", min_length=1, max_length=32)
    parent_id: str | None = Field(default=None, min_length=1, max_length=128)
    priority: int = Field(default=0, ge=-100000, le=100000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=256)
    deadline_at: datetime | None = Field(default=None)


class ChatCreateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=20000)
    context: dict[str, Any] = Field(default_factory=dict)
    work_item_id: str | None = Field(default=None, min_length=1, max_length=128)


class QwenFixRequest(BaseModel):
    type: str | None = Field(default="unknown", max_length=128)
    message: str = Field(..., min_length=1, max_length=10000)
    context: dict[str, Any] = Field(default_factory=dict)


class RunCreateRequest(BaseModel):
    work_item_id: str = Field(..., min_length=1, max_length=128)
    correlation_id: str | None = Field(default=None, min_length=1, max_length=64)
