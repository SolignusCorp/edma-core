# Contributing

Thanks for considering a contribution to **edma-core**.

## The constitution: frozen invariants

This project has a deterministic kernel by design. `docs/invariants.md` lists
the frozen invariants (13-state DOP, default-deny, no-PASS-without-evidence,
zero-template, memory-agnostic, fail-closed policy…). **CI runs the invariant
tests on every PR; PRs breaking them are rejected.** To change an invariant,
open an issue first — it requires a written rationale and a MAJOR version bump.

## Local development

```bash
git clone https://github.com/SolignusCorp/edma-core && cd edma-core
pip install -e ".[dev]"
pytest          # everything must stay green
ruff check src tests
```

## Ground rules

- **Test-first** for behavior changes; failed cases never hidden; no
  "production ready" claims without tests.
- **No silent catches of architectural errors** — fail closed, honestly.
- Core stays **dependency-free** (providers/plugins may use httpx etc.).
- No canned answers / keyword routers — the zero-template tests will find you.
- Keep handler code in plugins; `web.fetch`-style pure helpers in core only.
- One PR = one logical change; document behavioral deltas in CHANGELOG.md.

## Commit style

Short imperative subject; body explains WHY. Reference invariants/tests touched.
