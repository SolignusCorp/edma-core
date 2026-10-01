# Frozen Invariants (the project's constitution)

These invariants are enforced by TESTS that run on every PR. A PR that breaks
them will be rejected regardless of other merits. If an invariant should ever
change, it is a BREAKING MAJOR release with a written rationale — never a
drive-by PR.

1. **Exactly 13 DOP states** with the frozen legal transition table
   (`docs/dop-graph.md`). The DOP Engine is the sole transition authority;
   `runtime` is the only caller of `dop.decide()`.
   → `tests/test_edma_dop.py`, `tests/test_edma_component_integrity.py`

2. **Exactly 6 cognitive components** (Escalation is control flow, not a
   component).

3. **Memory-agnostic**: no memory subsystem, no memory state, no memory
   actions, no memory fields in traces. Conversation context is
   application-provided read-only input.
   → `tests/test_edma_memory_agnostic.py`

4. **No PASS without evidence**: simulated/heuristic output is ineligible as
   evidence; `SKIPPED` is not `PASS`; `UNCERTAIN` is never promoted.
   → `tests/test_edma_verification.py`

5. **Zero-template**: user-facing text comes ONLY from the model. Heuristic
   fallbacks never write answers; unknown entities have NO special branch;
   no canned response strings in production sources.
   → `tests/test_zero_template_core.py`

6. **No keyword triggers**: semantic decisions only (the deterministic layer
   contains no phrase→behavior routing).
   → `tests/test_zero_template_core.py`, hardening tests

7. **Default-deny actions**: unregistered action types are denied; the model
   cannot self-authorize; human approval only via the runtime API.
   → `tests/test_edma_hardening.py` (registry + approval tests)

8. **Fail-closed model policy**: one default model per deployment
   (`set_default_model`); unknown model ids raise `ModelPolicyError` — never a
   silent switch.
   → `tests/test_edma_hardening.py`

9. **Traces are honest and scrubbed**: entries only for executed states;
   secrets redacted; no fabricated narratives.
   → `tests/test_edma_hardening.py` (trace tests)

10. **No eval/exec/shell** anywhere in the package.
    → enforced by review + `docs/threat-model.md` T8 contract
