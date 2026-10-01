"""EDMA-CORE DEFAULT PORT IMPLEMENTATIONS — zero-config, stdlib-first.

InMemoryDocStore / ListEventSink / NullBudget let the runtime run out of the
box (demos, tests, embedded use). OpenAICompatProvider talks to ANY
OpenAI-compatible endpoint (groq, openai, vLLM, ollama, ...) — the default
configuration follows the benchmark-validated groq/qwen pairing.

FileDocStore persists runs as JSON files (~/.edma by default) for single-node
production-ish use without a database.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

from . import ports


# ─────────────────────────────────────────────────────────── DocStore
class InMemoryDocStore:
    """Process-local KV documents. Per-org isolation by uid."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.docs: dict[tuple[str, str], dict] = {}

    def user_doc_get(self, uid: str, key: str) -> Optional[dict]:
        with self._lock:
            doc = self.docs.get((str(uid), str(key)))
        return json.loads(json.dumps(doc, default=str)) if doc is not None else None

    def user_doc_put(self, uid: str, key: str, data: dict) -> bool:
        with self._lock:
            self.docs[(str(uid), str(key))] = json.loads(json.dumps(data, default=str))
        return True


class FileDocStore(InMemoryDocStore):
    """JSON files under a root dir: <uid sanitized>/<key sanitized>.json."""

    def __init__(self, root: Optional[str] = None) -> None:
        super().__init__()
        self.root = Path(root or os.environ.get("EDMA_DOCS_DIR", "~/.edma")).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, uid: str, key: str) -> Path:
        safe_uid = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(uid))[:80]
        safe_key = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(key))[:80]
        d = self.root / safe_uid
        d.mkdir(parents=True, exist_ok=True)
        return d / (safe_key + ".json")

    def user_doc_get(self, uid: str, key: str) -> Optional[dict]:
        p = self._path(uid, key)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def user_doc_put(self, uid: str, key: str, data: dict) -> bool:
        try:
            self._path(uid, key).write_text(
                json.dumps(json.loads(json.dumps(data, default=str)), ensure_ascii=False),
                encoding="utf-8")
            return True
        except Exception:
            return False


# ─────────────────────────────────────────────────────────── EventSink
class ListEventSink:
    """In-memory append-only audit list (bounded per uid+kind)."""

    def __init__(self, cap: int = 500) -> None:
        self.cap = int(cap)
        self._lock = threading.Lock()
        self.events: dict[tuple[str, str], list[dict]] = {}

    def event_log(self, uid: str, kind: str, entry: dict) -> None:
        with self._lock:
            lst = self.events.setdefault((str(uid), str(kind)), [])
            lst.append(json.loads(json.dumps(entry, default=str)))
            del lst[:-self.cap]

    def events_list(self, uid: str, kind: str, limit: int = 100) -> list:
        with self._lock:
            lst = list(self.events.get((str(uid), str(kind)), []))
        return lst[-max(1, int(limit)):]


# ─────────────────────────────────────────────────────────── Budget
class NullBudget:
    """No metering: costs are zero and preflight ALWAYS passes (infinite balance).
    (balance=0 would mean "out of funds" and deterministic gates would deny.)"""

    def cost_of(self, spec: dict) -> float:
        return 0.0

    def charge(self, uid: str, cost: float, reason: str, meta: dict) -> None:
        return None

    def balance(self, uid: str) -> float:
        return float("inf")


class TokenBudget:
    """Simple token-bucket-ish budget: charges cost per 1M tokens at rate."""

    def __init__(self, price_per_1m_in: float = 0.70, price_per_1m_out: float = 0.99,
                 initial_balance: float = float(os.environ.get("EDMA_BUDGET", "10") or 10)) -> None:
        self.pin = float(price_per_1m_in)
        self.pout = float(price_per_1m_out)
        self.balances: dict[str, float] = {}
        self.init = float(initial_balance)
        self._lock = threading.Lock()

    def cost_of(self, spec: dict) -> float:
        p = max(0, int(spec.get("prompt") or 0))
        c = max(0, int(spec.get("completion") or 0))
        return p / 1_000_000 * self.pin + c / 1_000_000 * self.pout

    def charge(self, uid: str, cost: float, reason: str, meta: dict) -> None:
        with self._lock:
            cur = self.balances.get(str(uid), self.init)
            self.balances[str(uid)] = cur - float(cost)

    def balance(self, uid: str) -> float:
        with self._lock:
            return self.balances.get(str(uid), self.init)


# ─────────────────────────────────────────────────────────── ModelProvider
_DEFAULT_BASE_URL = os.environ.get("EDMA_PROVIDER_BASE_URL", "https://api.groq.com/openai/v1")
_DEFAULT_MODEL = os.environ.get("EDMA_PROVIDER_MODEL", "qwen/qwen3.8-27b")


class OpenAICompatProvider:
    """Any OpenAI-compatible /chat/completions endpoint.

    Returns the uniform balancer.chat-shaped dict (never raises for ordinary
    failures). HTTP layer: httpx (optional dependency — imported lazily so the
    core itself stays dependency-free).
    """

    def __init__(self, api_key: Optional[str] = None, model: str = "",
                 base_url: str = "", timeout: float = 60.0,
                 provider_name: str = "openai-compat") -> None:
        self.api_key = api_key or os.environ.get("EDMA_PROVIDER_KEY", "")
        self.model = model or _DEFAULT_MODEL
        self.base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self.timeout = float(timeout)
        self.name = provider_name

    async def chat(self, payload: dict) -> dict:
        try:
            import httpx  # optional
        except Exception:
            return {"ok": False, "reply": "", "usage": None, "provider": self.name,
                    "attempts": [], "error": "httpx_not_installed", "byok": False}
        body = {"model": payload.get("model") or self.model,
                "messages": payload.get("messages") or [],
                "max_tokens": int(payload.get("max_tokens") or 512)}
        if payload.get("temperature") is not None:
            body["temperature"] = payload["temperature"]
        if payload.get("response_format"):
            body["response_format"] = payload["response_format"]
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as cl:
                r = await cl.post(f"{self.base_url}/chat/completions",
                                  json=body, headers=headers)
        except Exception as e:
            return {"ok": False, "reply": "", "usage": None, "provider": self.name,
                    "attempts": [f"{self.name}:err({str(e)[:60]})"],
                    "error": f"provider_failed: {str(e)[:160]}", "byok": False}
        if r.status_code != 200:
            return {"ok": False, "reply": "", "usage": None, "provider": self.name,
                    "attempts": [f"{self.name}:http({r.status_code})"],
                    "error": f"http_{r.status_code}", "byok": False}
        try:
            data = r.json()
            text = data["choices"][0]["message"]["content"] or ""
        except Exception:
            return {"ok": False, "reply": "", "usage": None, "provider": self.name,
                    "attempts": [f"{self.name}:bad_shape"],
                    "error": "malformed_provider_response", "byok": False}
        u = (data.get("usage") or {}) or {}
        usage = None
        if u.get("total_tokens"):
            usage = {"prompt_tokens": int(u.get("prompt_tokens") or 0),
                     "completion_tokens": int(u.get("completion_tokens") or 0),
                     "total_tokens": int(u.get("total_tokens") or 0), "source": "provider"}
        return {"ok": True, "reply": text, "usage": usage, "provider": self.name,
                "attempts": [], "error": None, "byok": False}


def wire_defaults(*, docs: bool = True, sink: bool = True, budget: bool = True) -> dict:
    """Zero-config wiring: in-memory docs/sink + NullBudget (only unset ports)."""
    cur = ports.snapshot()
    if docs and not cur["docs"]:
        ports.set_docs(InMemoryDocStore())
    if sink and not cur["sink"]:
        ports.set_sink(ListEventSink())
    if budget and not cur["budget"]:
        ports.set_budget(NullBudget())
    return ports.snapshot()


def env_provider() -> OpenAICompatProvider:
    """Provider from environment (EDMA_PROVIDER_KEY / _BASE_URL / _MODEL)."""
    return OpenAICompatProvider()


# default prices documented for TokenBudget (2026-10-01 platform parity):
#   input $0.70 / 1M tokens, output $0.99 / 1M tokens — billing to the nano-dollar.
_DEFAULTS_STAMP = time.time()
