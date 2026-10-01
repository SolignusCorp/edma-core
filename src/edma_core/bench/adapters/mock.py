"""§18 MockModelAdapter — deterministik, scripted; real model ulanmaguncha."""
from __future__ import annotations

import json
import time

from .base import BenchmarkModelAdapter, GenResult


def _est_tokens(text: str) -> int:
    return max(1, len(str(text or "")) // 4)


class MockModelAdapter(BenchmarkModelAdapter):
    """turns: javoblar ketma-ketligi (string yoki dict). Qisqsa — oxirgi turn qaytadi."""
    name = "mock"

    def __init__(self, turns: list, model_id: str = "mock-scripted-v1"):
        self.turns = [t if isinstance(t, str) else json.dumps(t, ensure_ascii=False)
                      for t in (turns or [""])]
        self.model_id = model_id
        self.calls = 0

    def generate(self, messages: list, temperature: float = 0.2,
                 max_tokens: int = 1024) -> GenResult:
        t0 = time.perf_counter()
        idx = min(self.calls, len(self.turns) - 1)
        text = self.turns[idx]
        self.calls += 1
        in_tok = sum(_est_tokens(m.get("content")) for m in messages or [])
        return GenResult(text=text, provider="mock", model_id=self.model_id,
                         usage={"input_tokens": in_tok, "output_tokens": _est_tokens(text)},
                         latency_ms=(time.perf_counter() - t0) * 1000, raw={"call_index": idx})

    def metadata(self) -> dict:
        return {"adapter": self.name, "model_id": self.model_id,
                "model_version": "scripted", "deterministic": True}
