"""Контракт planner (Vision/Epic/Story → дерево задач до atom)."""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field


class PlannerInput(BaseModel):
    """Вход для planner: Vision или любой work_item для декомпозиции."""

    model_config = ConfigDict(extra="forbid")

    work_item_id: str
    title: str
    description: str = ""
    kind: str  # vision, epic, story
    current_depth: int = 0
    max_depth: int = 4


class PlannerOutputItem(BaseModel):
    """Один элемент декомпозиции."""

    model_config = ConfigDict(extra="forbid")

    title: str
    description: str
    kind: Literal["epic", "story", "task", "atom"]
    files: List[str] = Field(default_factory=list)
    children: List["PlannerOutputItem"] = Field(default_factory=list)


class PlannerOutput(BaseModel):
    """Результат работы planner."""

    model_config = ConfigDict(extra="forbid")

    items: List[PlannerOutputItem]
    reasoning: str = ""

