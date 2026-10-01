# Model Card — EDMA Runtime (core + default provider config)

**Model**: EDMA runtime (Evidence-Driven Model Architecture) around ONE
foundation model. Default provider config: `groq / qwen3.8-27b`
(benchmark-validated pairing). Version: core `0.1.0`, runtime policy
`13-state-evidence-driven`.

**Organizations**: SolignusCorp. **Date**: 2026-10-01.

## What the runtime guarantees

- Discipline: no PASS without deterministic evidence; default-deny actions;
  model cannot self-authorize; answers only from the model (zero-template).
- Auditability: canonical trace per executed state + append-only audit sink.
- Resumability: snapshots around every transition; bounded, honest failures.

## What it does NOT guarantee (honest limits)

- It does not make the underlying model more intelligent.
- Small-n benchmarks below do NOT establish statistical superiority
  (95% CIs overlap). The claimed value is discipline, not accuracy wins.

## Benchmark numbers (verbatim, independent evaluator; the model never grades itself)

| Suite (n) | CONTROL (direct chat) | EDMA runtime | Notes |
|---|---|---|---|
| world-v2 multi-domain (42) | 83.3% CI [69.4–91.7] | 90.5% CI [77.9–96.2] | structured tasks 60%→90%; 0 execution errors |
| world-v1 GSM8K+MMLU (30) | 84.0% | 83.3% | accuracy preserved; ~3.6× more model calls |
| golden-v1 tool cases (15) | 20% success, hallucination 13.3% | 40% success, hallucination 0% | verification gate filters fabricated success |

- Evaluator: independent scripts + case files; failed cases are never hidden;
  `UNCERTAIN` is never promoted.
- Overhead: world-v2 ~3.4× wall-clock vs direct chat (extra cognitive calls +
  verification). This is the price of discipline.
- n is small (15–42); CIs overlap → **no superiority claim**.

## Intended / non-intended use

- Intended: agents that must ACT safely (tools, writes, approvals) and be
  auditable; datasets authored by direct model calls with QC.
- Non-intended: as a general chat model; as a guarantee of factual truth;
  unattended high-risk actions without an approval backend.

## Training data / provenance

The core contains NO training code and NO weights. Provenance belongs to the
chosen foundation model provider (see their model card).
