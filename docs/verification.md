# Verification Model — PASS / FAIL / UNCERTAIN / SKIPPED

Verification (`edma_core.verification`) judges **real-world effects**, never
claims. It is deterministic where evidence permits; semantic residual risk is
explicitly handed back to REASONING, not papered over with regex.

## Verdicts

| Verdict | Meaning |
|---|---|
| `PASS` | Deterministic evidence confirms the effect (read-back match, content-bearing results, hash evidence). |
| `FAIL` | Evidence confirms the effect did NOT happen (no results, absent record, count mismatch, transport error). |
| `UNCERTAIN` | Cannot confirm NOR deny honestly: simulated/mock source, unusable evidence shape, reader unavailable. |
| `SKIPPED` | The check did not run. **SKIPPED is NOT PASS.** |

## Never trusted

`verified=true`, `success=true`, model confidence, model assertions, bare
function returns. `simulated`/heuristic output is **ineligible as evidence**
(it maps to `UNCERTAIN`, never `PASS`).

## Rules per action (core + contrib contract)

### `web.search` (contrib plugin; rule lives in core)
- provider `mock`/absent → `UNCERTAIN` (simulated results are not real-world evidence)
- zero results → `FAIL`
- results lack `http`-URL + non-empty content → `UNCERTAIN` (title alone is insufficient)
- otherwise → `PASS` with up-to-5 `claims[]` evidence summary (source_ref / claim / excerpt);
  **semantic claim↔question relevance is decided by REASONING** — not by lexical regex.

### `web.fetch` (core)
- non-200 / transport error → `FAIL` with url evidence
- missing `https` URL, `sha16`, or char count → `UNCERTAIN` (incomplete evidence)
- otherwise → `PASS` (`sha16` content-hash + byte count)

### `dataset.create` (contrib plugin)
- creation error / missing dataset id → `FAIL`
- reader unavailable → `UNCERTAIN` (honest — no read-back, no PASS)
- record absent after create → `FAIL` (read-back)
- sample-count mismatch → `FAIL`
- read-back matches → `PASS`

### unknown action
- → `UNCERTAIN` with reason `no verification rule for action … — not assumed PASS`.

## Design notes

- Read-back is the canonical evidence pattern: effects are verified against
  STORAGE, not against the executor's own report (executor and verifier are
  separated — the handler never grades itself).
- `UNCERTAIN` is **never promoted**: it routes back into REASONING (more
  specific query, alternative evidence, or an honest statement of uncertainty).
- Verdicts carry `evidence_summary` — the trace shows WHY, auditable later
  through the EventSink.
