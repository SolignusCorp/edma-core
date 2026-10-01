# Canonical Trace & Audit Spec

Two layers (`edma_core.trace`), both showing what ACTUALLY happened — never a
pre-composed narrative:

## 1. In-run trace (`run["trace"]`)
- Authoritative, persisted WITH the run snapshot via the DocStore port
  (survives requests → resumability).
- One entry per EXECUTED DOP state:

```json
{
  "run_id": "edmarun_…", "seq": 4,
  "state": "ACTION_AUTHORIZATION",
  "decision": {"next": "ACTION", "reason": "auto (risk=medium)"},
  "component": {"mode": "…", "ok": true, "source": "model", "provider": "…"},
  "facts": {"…facts summary…"},
  "ts": 1759330000000
}
```

- Entries exist ONLY for transitions/components that actually executed —
  trajectories naturally vary per request (zero-template applies to traces too).

## 2. Durable audit (EventSink port)
- Append-only, **fail-soft**: sink errors never block the run.
- `kind="edma_trace"`; reconstruction = snapshot trace + events filtered by
  `run_id` (`trace.reconstruct(uid, run_id, run_doc)`).

## Scrubbing (mandatory, both layers)
- Long strings truncated (`MAX_TEXT=300`, dicts to depth 4, lists to 12).
- Secret-shaped strings are redacted by prefix:
  `sk-ts-, Bearer , gsk_, sk-or-, sk-` → `…[redacted]`.
- Credentials never enter traces (platform mandate, carried into core).

## Resumability
- Snapshots re-written around EVERY transition (before each component runs),
  so a crash mid-component resumes by re-entering that state.
- `ACTION` records an at-most-once marker; verification catches an absent
  real-world effect if execution happened but crashed after.
- Durable leases (`LEASE_TTL_MS`) bound worker ownership for pending runs;
  `discover_pending()` finds resumable runs from per-org indexes.
