from __future__ import annotations

from typing import Any
import unittest

from factory.llm import LLMProvider


class _DummyProvider:
    async def generate(self, prompt: str, **kwargs: Any) -> str:
        return prompt

    async def embed(self, text: str) -> list[float]:
        return [0.0]


class LLMProviderProtocolTests(unittest.TestCase):
    def test_runtime_checkable_protocol_accepts_structural_implementation(self) -> None:
        provider = _DummyProvider()
        self.assertIsInstance(provider, LLMProvider)


if __name__ == "__main__":
    unittest.main()
