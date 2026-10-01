"""EDMA-CORE PORTS — the ONLY seams between the cognitive runtime and the world.

The core package is platform-free: it never imports a store, wallet, tiers,
balancer or HTTP framework. Everything environment-specific arrives through
the five ports below (duck-typed Protocols + module-level registration).

Contracts (frozen, implementation task §4-compatible):
  DocStore       — per-org (uid) key/value documents: run snapshots + index.
  EventSink      — append-only durable audit stream (fail-soft by design).
  Budget         — optional metering/charging of model calls and action costs.
  ModelProvider  — ONE chat-completion provider ({ok, reply, usage, ...} dict,
                   the OpenAI-ish shape `balancer.chat` uses in the SaaS).
  DatasetReader  — read-back access for dataset verification (evidence rules).

Defaults (defaults.py): in-memory implementations so the runtime works with
ZERO configuration; production deployments inject their own via configure().
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Protocol, runtime_checkable


# ─────────────────────────────────────────────────────────── port protocols
@runtime_checkable
class DocStore(Protocol):
    """Per-org document store: run snapshots + run index (KV documents)."""

    def user_doc_get(self, uid: str, key: str) -> Optional[dict]: ...

    def user_doc_put(self, uid: str, key: str, data: dict) -> bool: ...


@runtime_checkable
class EventSink(Protocol):
    """Append-only audit stream. Implementations MUST be fail-soft friendly
    (the runtime treats sink errors as non-fatal)."""

    def event_log(self, uid: str, kind: str, entry: dict) -> None: ...

    def events_list(self, uid: str, kind: str, limit: int = 100) -> list: ...


@runtime_checkable
class Budget(Protocol):
    """Optional metering. cost_of() prices a token usage dict; charge() records
    the spend; balance() answers pre-flight checks (>= 0)."""

    def cost_of(self, spec: dict) -> float: ...

    def charge(self, uid: str, cost: float, reason: str, meta: dict) -> None: ...

    def balance(self, uid: str) -> float: ...


@runtime_checkable
class ModelProvider(Protocol):
    """Single chat-completion provider. MUST return the uniform dict:
    {'ok': bool, 'reply': str, 'usage': {'prompt_tokens','completion_tokens',
    'total_tokens'} | None, 'provider': str, 'error': str|None, 'byok': bool}
    and MUST NOT raise for ordinary failures (return ok=False instead)."""

    async def chat(self, payload: dict) -> dict: ...


@runtime_checkable
class DatasetReader(Protocol):
    """Read-back access used ONLY by dataset.create verification evidence."""

    def get_dataset(self, ds_id: str, uid: str) -> Optional[dict]: ...


# ─────────────────────────────────────────────────────────── registry
_provider: Optional[ModelProvider] = None
_docs: Optional[DocStore] = None
_sink: Optional[EventSink] = None
_budget: Optional[Budget] = None
_dataset_reader: Optional[DatasetReader] = None
_user_profile: Optional[Callable[[str], dict]] = None


def set_provider(p: Optional[ModelProvider]) -> None:
    global _provider
    _provider = p


def set_docs(d: Optional[DocStore]) -> None:
    global _docs
    _docs = d


def set_sink(s: Optional[EventSink]) -> None:
    global _sink
    _sink = s


def set_budget(b: Optional[Budget]) -> None:
    global _budget
    _budget = b


def set_dataset_reader(r: Optional[DatasetReader]) -> None:
    global _dataset_reader
    _dataset_reader = r


def set_user_profile(fn: Optional[Callable[[str], dict]]) -> None:
    """Optional per-org profile lookup: {'tier': int, 'locale': 'uz', ...}.
    Core never requires it; used only for locale fallback / tier caps."""
    global _user_profile
    _user_profile = fn


def get_provider() -> Optional[ModelProvider]:
    return _provider


def get_docs() -> Optional[DocStore]:
    return _docs


def get_sink() -> Optional[EventSink]:
    return _sink


def get_budget() -> Optional[Budget]:
    return _budget


def get_dataset_reader() -> Optional[DatasetReader]:
    return _dataset_reader


def get_user_profile() -> Optional[Callable[[str], dict]]:
    return _user_profile


def configure(*, provider: Optional[ModelProvider] = None,
              docs: Optional[DocStore] = None,
              sink: Optional[EventSink] = None,
              budget: Optional[Budget] = None,
              dataset_reader: Optional[DatasetReader] = None,
              user_profile: Optional[Callable[[str], dict]] = None) -> None:
    """One-call wiring of every port (idempotent; None = leave unchanged)."""
    if provider is not None:
        set_provider(provider)
    if docs is not None:
        set_docs(docs)
    if sink is not None:
        set_sink(sink)
    if budget is not None:
        set_budget(budget)
    if dataset_reader is not None:
        set_dataset_reader(dataset_reader)
    if user_profile is not None:
        set_user_profile(user_profile)


def reset() -> None:
    """Test seam: back to pristine (defaults are re-created lazily)."""
    global _provider, _docs, _sink, _budget, _dataset_reader, _user_profile
    _provider = _docs = _sink = _budget = _dataset_reader = _user_profile = None


def snapshot() -> dict[str, Any]:
    """Diagnostics: which ports are wired (never the objects themselves)."""
    return {"provider": _provider is not None, "docs": _docs is not None,
            "sink": _sink is not None, "budget": _budget is not None,
            "dataset_reader": _dataset_reader is not None,
            "user_profile": _user_profile is not None}
