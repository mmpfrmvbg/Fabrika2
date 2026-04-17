from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    async def generate(self, prompt: str, **kwargs: Any) -> str: ...

    async def embed(self, text: str) -> list[float]: ...
