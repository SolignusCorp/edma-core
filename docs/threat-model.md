# Threat Model — why default-deny

Scope: an LLM agent whose model output is UNTRUSTED INPUT to a deterministic
kernel. EDMA treats the model like a brilliant but untrusted advisor: it can
propose, never decide.

## Threats and controls

| # | Threat | Control |
|---|---|---|
| T1 | Model invents a capability ("run shell command X") | **Default-deny registry**: unregistered action types → `denied` deterministically; `ACTION_DECISION` records `registered=false` as a FACT; execution impossible |
| T2 | Model self-authorizes (`"authorized": true` in output) | Control fields scrubbed into `Facts.ignored_model_fields`; authorization inputs can never contain model opinions |
| T3 | Model fabricates tool success | Evidence-based verification: read-back / hashes / provider facts; simulated output ineligible → `UNCERTAIN`, never `PASS` |
| T4 | Human-approval bypass | Approval arrives ONLY through the runtime API (`answer_run(approve=True|False)`); model cannot set it; public/unattended contexts should auto-deny |
| T5 | Prompt-injected "memory" manipulation | **Memory-agnostic core**: no memory subsystem exists to attack; conversation context is read-only application input; the runtime never mutates it |
| T6 | Infinite loops / resource burn | Semantic non-progress detection (fingerprint repeats → ESCALATION_CHECK) + hard step cap as secondary net + per-run model-call budget (`max_model_calls`) |
| T7 | Secret leakage through traces | Mandatory scrubbing by prefix (`sk-`, `Bearer `, …) + truncation in both trace layers |
| T8 | Eval/exec injection through handlers | No `eval`/`exec`/`shell=True` anywhere; handlers are explicit, bounded, dependency-injected functions |
| T9 | Confused-deputy cross-org access | Runs are per-org (`uid`) documents; `get_run` refuses cross-org loads; isolation tested |

## Residual risks (honest)

- Semantic correctness of answers is the MODEL's domain — EDMA guarantees
  discipline (evidence gates, authorization), not truth.
- A malicious handler REGISTERED by the deployer is inside the trust boundary —
  plugins are deployer-supplied code (review what you register).
- Denial-of-service on the provider layer manifests as honest `failed` /
  `pending` states; the runtime never fabricates content to fill the gap.
