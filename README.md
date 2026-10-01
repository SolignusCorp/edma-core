# EDMA-CORE

**EDMA — Evidence-Driven Model Architecture.** An evidence-first cognitive runtime
for AI agents: a frozen 13-state DOP graph, deterministic default-deny action
authorization, evidence-based verification, and a strict **zero-template** policy —
user-facing answers come ONLY from the model, never from canned text.

> Open core of the runtime that powers the Solignus AI platform in production.

## Why evidence-first?

Agent frameworks usually trust the model: the model says the tool succeeded → the
framework reports success. EDMA inverts this:

| Principle | Meaning |
|---|---|
| **Dalilsiz PASS yo'q** | Verification judges real-world effects (read-back, hashes, provider facts) — never model claims. `UNCERTAIN` is never promoted. |
| **Default-deny actions** | Unregistered action types are denied. The model cannot self-authorize: `authorized`/`approved` fields are scrubbed and ignored. |
| **Zero-template** | Heuristic fallbacks never invent user-facing text. If the model didn't write it, it isn't said. |
| **Memory-agnostic** | No memory subsystem. Conversation context is application-provided input; the runtime never mutates it. |
| **Deterministic control** | Exactly 13 DOP states with a frozen transition table. The DOP Engine is the sole transition authority; components/model return facts only. |

## Quickstart

```bash
pip install edma-core[provider]        # httpx only for the default provider
export EDMA_PROVIDER_KEY="gsk_..."     # any OpenAI-compatible key
export EDMA_PROVIDER_BASE_URL="https://api.groq.com/openai/v1"
export EDMA_PROVIDER_MODEL="qwen/qwen3.8-27b"
```

```python
import asyncio
from edma_core import quickstart, runtime as RT

quickstart()  # env provider + in-memory docs/audit + no-budget

run = RT.start_run("org-1", "What is 17*24?")
view = asyncio.run(RT.advance_run("org-1", run))
print(view["response"])            # model-authored answer (or honest failure state)
print(view["dop_path"])            # canonical trajectory: START→PERCEIVING→REASONING→RESPONSE
```

Zero configuration also works (no provider → deterministic heuristics and honest
`failed`/`pending` states instead of fabricated answers).

## Architecture

```
        ┌────────────────────── runtime.py (resumable DOP loop) ─────────────────────┐
        │  the ONLY caller of dop.decide()                                           │
        │                                                                            │
 model ─┼─▶ PERCEIVING ─▶ ENERGY ─▶ REASONING ─▶ ACTION_DECISION ─▶ ACTION_AUTHORIZATION
        │       (13 frozen states; waiting states pause WITHOUT transitioning)       │
        │                                    ▲                       │ approve/deny │
        │                                    └── VERIFICATION ◀── ACTION (execute)   │
        │                                                │                           │
        │                                            RESPONSE ─▶ END                 │
        └────────────────────────────────────────────────────────────────────────────┘
 ports:  ModelProvider (chat) · DocStore (run snapshots) · EventSink (audit)
         Budget (metering, optional) · DatasetReader (read-back evidence)
```

- `dop.py` — frozen 13-state graph + deterministic decision policy
- `components.py` — ONE model, four structured cognitive modes
  (perceive / energy / reason / respond) + honest heuristic fallbacks
- `actions.py` — registry + deterministic ACTION_AUTHORIZATION (default-deny);
  capabilities are **plugins** (`contrib.py` factories, dependency-injected)
- `verification.py` — PASS / FAIL / UNCERTAIN / SKIPPED from evidence only
- `trace.py` — canonical trace of what ACTUALLY executed (+ durable audit sink)
- `runtime.py` — resumable loop: snapshots re-written around every transition,
  human-approval pauses, semantic non-progress detection, hard step cap as
  a secondary safety net only
- `dataset_exec.py` — fast direct-model dataset generation with QC
  (no samples pass QC → no dataset; nothing fabricated)

## Wiring your own backends

```python
from edma_core import configure

configure(
    provider=MyProvider(),        # async chat(payload) -> {ok, reply, usage, ...}
    docs=MyDocStore(),            # user_doc_get/user_doc_put per-org documents
    sink=MyAuditSink(),           # event_log/events_list append-only audit
    budget=MyBudget(),            # cost_of/charge/balance (optional)
    dataset_reader=MyReader(),    # get_dataset for read-back verification
)
```

## Honest benchmarks (small n — no superiority claims)

Controlled runs (independent evaluator, the model never grades itself;
95% CIs OVERLAP — we do not claim statistical superiority):

| Suite (n) | CONTROL (direct chat) | EDMA runtime | Notes |
|---|---|---|---|
| world-v2 multi-domain (42) | 83.3% [69.4–91.7] | 90.5% [77.9–96.2] | structured tasks 60% → 90%; 0 execution errors |
| world-v1 GSM8K+MMLU (30) | 84.0% | 83.3% | accuracy preserved; ~3.6× more model calls |
| golden-v1 tool cases (15) | 20% success | 40% success | hallucination 0% (EDMA) vs 13.3% |

The value proposition is **discipline**: no PASS without evidence, forbidden
actions stop, answers re-checked — at the cost of extra model calls.

## Documentation

- [docs/dop-graph.md](docs/dop-graph.md) — the frozen 13-state DOP graph (mermaid + table)
- [docs/verification.md](docs/verification.md) — PASS/FAIL/UNCERTAIN/SKIPPED evidence rules
- [docs/trace-and-audit.md](docs/trace-and-audit.md) — canonical trace + durable audit spec
- [docs/threat-model.md](docs/threat-model.md) — why default-deny; residual risks
- [docs/invariants.md](docs/invariants.md) — the project's constitution (PR-gated)
- [MODEL_CARD.md](MODEL_CARD.md) — verbatim-honest benchmark numbers
- [CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) · [CHANGELOG.md](CHANGELOG.md)

## Development

```bash
pip install -e ".[dev]"
pytest          # 151 tests: DOP policy, verification evidence rules, runtime
                # trajectories, hardening, memory-agnostic + zero-template invariants
```

Invariant tests (13 states, default-deny, zero-template, memory-agnostic) run on
every PR and are the project's constitution — changes to them will be rejected.

## License

Apache-2.0. `edma-core` is the open core; the managed platform (Solignus AI) is
a separate product.
