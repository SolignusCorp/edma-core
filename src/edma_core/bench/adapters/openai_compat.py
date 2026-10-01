"""OpenAICompatAdapter — ANY OpenAI-compatible endpoint (bench adapter).

Round-robin BIR NECHA kalit ustida (har kalit alohida hisob/limit); 429/quota/
rate limit'da KEY ROTATSIYA. Kalitlar FAQAT env'dan (EDMA_BENCH_KEY_N /
EDMA_BENCH_KEYS) — repo/trace/log'larga KIRMAYDI.

Env:
  EDMA_BENCH_BASE_URL  (default: https://api.groq.com/openai/v1)
  EDMA_BENCH_MODEL     (default: qwen/qwen3.8-27b — benchmark-validated;
                        gpt-oss standing qaror bo'yicha ISHLATILMAYDI)
  EDMA_BENCH_KEY_N / EDMA_BENCH_KEYS (vergul bilan)
"""
from __future__ import annotations

import json
import os

RETRYABLE_STATUS = (429, 500, 502, 503)

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None

DEFAULT_BASE_URL = os.environ.get("EDMA_BENCH_BASE_URL", "https://api.groq.com/openai/v1")
DEFAULT_MODEL = os.environ.get("EDMA_BENCH_MODEL", "qwen/qwen3.8-27b")


def keys_from_env() -> list:
    keys = []
    for i in range(1, 17):
        v = os.environ.get(f"EDMA_BENCH_KEY_{i}")
        if v:
            keys.append(v.strip())
    raw = os.environ.get("EDMA_BENCH_KEYS")
    if raw:
        keys.extend([k.strip() for k in raw.split(",") if k.strip()])
    legacy = os.environ.get("GROQ_API_KEY", "").strip()
    if legacy:
        keys.append(legacy)
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


from .base import BenchmarkModelAdapter, GenResult


class OpenAICompatAdapter(BenchmarkModelAdapter):
    name = "openai-compat"

    def __init__(self, model_id: str = "", api_keys: list = None,
                 temperature: float = 0.2, max_tokens: int = 2048,
                 timeout_s: float = 120.0, base_url: str = "",
                 max_rounds: int = 2, client=None):
        if httpx is None:
            raise RuntimeError("httpx_not_installed: pip install 'edma-core[provider]'")
        self.model_id = str(model_id or DEFAULT_MODEL)
        self.api_keys = list(api_keys) if api_keys else keys_from_env()
        if not self.api_keys:
            raise RuntimeError("BENCH_KEYS_EMPTY: EDMA_BENCH_KEY_N env'da topilmadi "
                               "(kalitlar faqat env'dan o'qiladi)")
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)
        self.timeout_s = float(timeout_s)
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.max_rounds = int(max_rounds)
        self.client = client or httpx.Client(timeout=self.timeout_s)
        self._rr = 0
        self.key_errors = {}

    def _next_key(self):
        k = self.api_keys[self._rr % len(self.api_keys)]
        self._rr += 1
        return k

    def generate(self, messages: list, temperature: float = None,
                 max_tokens: int = None) -> GenResult:
        body = {
            "model": self.model_id,
            "messages": [{"role": m.get("role"), "content": str(m.get("content") or "")}
                         for m in (messages or [])],
            "temperature": self.temperature if temperature is None else float(temperature),
            "max_tokens": self.max_tokens if max_tokens is None else int(max_tokens),
        }
        url = self.base_url + "/chat/completions"
        attempts = max(1, len(self.api_keys) * self.max_rounds)
        last_err = "no attempts"
        for _ in range(attempts):
            idx = self._rr % len(self.api_keys)
            key = self._next_key()
            try:
                resp = self.client.post(url, json=body,
                                        headers={"Authorization": f"Bearer {key}"})
            except (httpx.TimeoutException, httpx.TransportError,
                    TimeoutError, ConnectionError) as e:
                last_err = f"transport: {type(e).__name__}"
                self.key_errors[idx] = last_err
                continue
            if resp.status_code in RETRYABLE_STATUS:
                last_err = f"http_{resp.status_code}"
                self.key_errors[idx] = last_err
                continue
            if resp.status_code in (401, 403):
                last_err = f"http_{resp.status_code}_key_rejected"
                self.key_errors[idx] = last_err
                continue
            if resp.status_code == 404:
                raise RuntimeError(f"MODEL_NOT_FOUND: {self.model_id} — {resp.text[:160]}")
            if resp.status_code == 400:
                err_text = str(resp.text or "")
                if "tool_use_failed" in err_text or "Tool choice is none" in err_text:
                    # provider-tomon transiyent rad (native tool harness) — retry
                    last_err = "http_400_tool_use_failed"
                    self.key_errors[idx] = last_err
                    continue
                raise RuntimeError(f"BENCH_BAD_REQUEST: {err_text[:160]}")
            if resp.status_code != 200:
                last_err = f"http_{resp.status_code}"
                self.key_errors[idx] = last_err
                continue
            try:
                data = resp.json()
            except json.JSONDecodeError:
                last_err = "bad_json"
                self.key_errors[idx] = last_err
                continue
            if "error" in data:
                msg = str((data.get("error") or {}).get("message") or "")[:160]
                low = msg.lower()
                if any(t in low for t in ("rate limit", "quota", "try again",
                                          "too many requests", "capacity")):
                    last_err = f"retryable: {msg[:80]}"
                    self.key_errors[idx] = last_err
                    continue
                raise RuntimeError(f"BENCH_API_ERROR: {msg}")
            try:
                ch = (data.get("choices") or [{}])[0]
                text = str((ch.get("message") or {}).get("content") or "")
                u = data.get("usage") or {}
                return GenResult(text=text, provider=self.name, model_id=self.model_id,
                                 usage={"input_tokens": int(u.get("prompt_tokens") or 0),
                                        "output_tokens": int(u.get("completion_tokens") or 0)},
                                 latency_ms=0.0,
                                 raw={"finish_reason": ch.get("finish_reason")})
            except Exception as e:
                last_err = f"shape: {type(e).__name__}"
                self.key_errors[idx] = last_err
                continue
        raise RuntimeError(f"BENCH_ALL_KEYS_EXHAUSTED ({attempts} urinish): {last_err}")

    def metadata(self) -> dict:
        return {"adapter": self.name, "model_id": self.model_id,
                "model_version": self.model_id, "keys_count": len(self.api_keys),
                "round_robin": True, "deterministic": False}
