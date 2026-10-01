"""§3 BenchmarkModelAdapter — provider-agnostic model interface.

Hozir: MockModelAdapter (§18 — real model ULANMAGAN).
Keyinroq: Ollama/Gemini/OpenAI-compatible adapterlar shu interfeysga ulanadi.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GenResult:
    text: str
    provider: str = "mock"
    model_id: str = "mock-1"
    usage: dict = field(default_factory=dict)      # {input_tokens, output_tokens}
    latency_ms: float = 0.0
    raw: Any = None


class BenchmarkModelAdapter(ABC):
    """Minimal interface: generate / stream / metadata. Har qanday provider
    (mock/Ollama/Gemini/OpenAI-compatible) SHU interfeysga ulanadi."""
    name = "base"

    @abstractmethod
    def generate(self, messages: list, temperature: float = 0.2,
                 max_tokens: int = 1024) -> GenResult:
        raise NotImplementedError

    def stream(self, messages: list, temperature: float = 0.2, max_tokens: int = 1024):
        """Iterator of text chunks (default: single chunk from generate)."""
        res = self.generate(messages, temperature=temperature, max_tokens=max_tokens)
        yield res.text

    def metadata(self) -> dict:
        return {"adapter": self.name, "model_id": "unknown", "model_version": "unknown"}
