"""EDMA-CORE — evidence-first cognitive runtime for AI agents (open core).

EDMA = Evidence-Driven Model Architecture. A frozen, deterministic cognitive
runtime around ONE foundation model:

  ports.py         — the ONLY seams to the world (DocStore/EventSink/Budget/
                     ModelProvider/DatasetReader); core is platform-free
  defaults.py      — zero-config in-memory implementations + OpenAI-compatible
                     provider (groq/qwen default, benchmark-validated)
  dop.py           — DOP Engine: frozen 13-state graph + deterministic policy
  components.py    — single CognitiveModel facade (structured modes) + honest
                     heuristic fallbacks
  actions.py       — Action Registry + deterministic ACTION_AUTHORIZATION
                     (default-deny; model cannot self-authorize)
  contrib.py       — OPTIONAL capability plugins (web.search, dataset.create)
                     as dependency-injected factories
  verification.py  — evidence-based verification (PASS/FAIL/UNCERTAIN/SKIPPED;
                     no evidence → never PASS; UNCERTAIN never promoted)
  trace.py         — canonical production trace (what ACTUALLY happened)
  runtime.py       — resumable DOP loop (the only caller of dop.decide)
  dataset_exec.py  — fast direct-model dataset generation with QC
  json_healer.py   — the deterministic JSON repair parser

Frozen invariants (implementation task §1–§4):
  - exactly 6 cognitive components (Escalation is control, not a component)
  - exactly 13 DOP states with the frozen legal transition table
  - MEMORY-AGNOSTIC: no memory subsystem — conversation context is
    application-provided input
  - the DOP Engine is the sole transition authority; components/model return
    facts only; model control fields are scrubbed and ignored
  - no canned/fabricated answers: responses come ONLY from the model
  - no keyword triggers: semantic decisions only

Zero-template policy: this core never invents user-facing text. If the model
does not answer, the runtime reports an honest failure state.
"""
__version__ = "0.1.0"

from . import dop  # noqa: F401  (re-export convenience)
from . import ports
from . import defaults


def configure(**kw) -> dict:
    """Wire ports (see edma_core.ports.configure) and return the wiring snapshot."""
    ports.configure(**kw)
    return ports.snapshot()


def quickstart(provider: "defaults.OpenAICompatProvider | None" = None) -> dict:
    """One-call setup for demos: env provider + in-memory docs/sink + NullBudget."""
    defaults.wire_defaults()
    if provider is None:
        provider = defaults.env_provider()
    ports.set_provider(provider)
    return ports.snapshot()
