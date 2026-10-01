# Changelog

All notable changes to **edma-core**. Format: Keep a Changelog; semver.

## [0.1.0] — 2026-10-01

### Added (F0+F1 — open-core extraction)
- Core runtime: frozen 13-state DOP graph + deterministic policy,
  resumable loop (snapshots, leases, pending discovery), semantic
  non-progress detection, model-field scrubbing.
- 5 ports (`ports.py`): ModelProvider / DocStore / EventSink / Budget /
  DatasetReader — the core is platform-free.
- Zero-config defaults (`defaults.py`): in-memory + file DocStore,
  list EventSink, NullBudget (infinite), OpenAI-compatible provider
  (env `EDMA_PROVIDER_KEY` / `EDMA_PROVIDER_BASE_URL` / `EDMA_PROVIDER_MODEL`).
- Action layer: default-deny registry, deterministic ACTION_AUTHORIZATION,
  bounded `web.fetch` in core; `web.search` / `dataset.create` as
  dependency-injected plugin factories (`contrib.py`).
- Evidence-based verification (PASS/FAIL/UNCERTAIN/SKIPPED; simulated
  output ineligible; read-back evidence for datasets).
- Canonical trace + durable audit sink with mandatory secret scrubbing.
- Direct-model dataset generation with QC (`dataset_exec.py`); honest
  `llm_direct` source meta; no template fabrication.
- Tests: 151 green (DOP, verification, runtime dynamics, hardening,
  memory-agnostic + zero-template invariants).
- Docs: DOP graph, verification model, trace/audit spec, threat model,
  frozen invariants, model card.

### Notes
- F0/F1 = open-core extraction from the production platform; behavioral
  parity of the core with its production runtime.
