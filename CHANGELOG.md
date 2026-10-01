# Changelog

All notable changes to **edma-core**. Format: Keep a Changelog; semver.

## [Unreleased]

(none yet)

## [0.1.0] — 2026-10-02

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
- Docs: DOP graph, verification model, trace/audit spec, threat model,
  frozen invariants, model card.

### Added (F2 — benchmark lab)
- `edma_core.bench` — the benchmark lab as a package: case sets (golden-v1,
  world-v1, world-v2), independent evaluator + contract auditor, metrics and
  Wilson-CI reports, deterministic scripted runs and real-model runs via any
  OpenAI-compatible endpoint (`edma-bench` / `python -m edma_core.bench`).
  Import-lint gate: only `bench/adapters/edma_binding.py` may import the EDMA
  core; the evaluator never does. No automatic winner; failed cases never
  hidden; `UNCERTAIN` never promoted.

### Added (F3 — MCP server + packaging)
- `edma_core.mcp` — Model Context Protocol server over stdio
  (newline-delimited JSON-RPC 2.0; protocol 2025-06-18, zero dependencies):
  `edma_authorize` (decision-only, default-deny, never executes),
  `edma_verify` (NO EVIDENCE → NO PASS on observable `ActionResult`),
  `edma_list_actions`, `edma_dop_graph`. Claude Desktop config example:
  `examples/claude_desktop_config.json`.
- Console scripts: `edma-bench`, `edma-mcp`; packaging metadata finalized
  (author SolignusCorp, URLs, extras); clean wheel/sdist via
  `python -m build` (case-set JSONs included).
- Tests: 108 bench + 25 MCP on top of 151 core = 284 green.

### Notes
- F0–F3 = open-core extraction from the production platform; behavioral
  parity of the core with its production runtime.


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
