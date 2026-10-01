"""EDMA production trace — what ACTUALLY happened, not a template.

Two layers:
  1. In-run trace: `run["trace"]` — authoritative, persisted with the run
     snapshot via the DocStore port; survives across requests (resumability).
  2. Durable audit: the EventSink port (append-only), fail-soft —
     never blocks the run, never stores secrets, content truncated.

Trace entries exist ONLY for transitions/components that actually executed, so
trajectories naturally vary per request. Reconstruction replays events by run_id
plus the snapshot.
"""
from __future__ import annotations

import re
import time
from typing import Optional

from . import ports

MAX_TEXT = 300
SECRET_PREFIXES = ("sk-ts-", "Bearer ", "gsk_", "sk-or-", "sk-")


def _clean(value, depth: int = 0):
    """Truncate long strings; scrub anything that looks like a secret."""
    if isinstance(value, str):
        out = value
        for p in SECRET_PREFIXES:
            if p.lower() in out.lower():
                out = re.sub(re.escape(p) + r"\S+", p.rstrip("-") + "…[redacted]", out, flags=re.I)
        return out[:MAX_TEXT]
    if isinstance(value, dict):
        return {k: _clean(v, depth + 1) for k, v in value.items() if depth < 4}
    if isinstance(value, (list, tuple)):
        return [_clean(v, depth + 1) for v in value[:12]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_TEXT]


def emit(run: dict, state: str, decision_dict: dict, component: dict,
         facts_summary: Optional[dict] = None) -> dict:
    """Record one executed DOP state: transition taken + component facts."""
    seq = len(run.get("trace") or []) + 1
    entry = _clean({
        "run_id": run.get("id"), "seq": seq, "state": state,
        "decision": decision_dict,           # {next, reason}
        "component": component,              # {mode/result summary per component}
        "facts": facts_summary or {},
        "ts": int(time.time() * 1000),
    })
    run.setdefault("trace", []).append(entry)
    sink = ports.get_sink()
    if sink is not None and run.get("uid"):
        try:  # durable audit — fail-soft
            sink.event_log(run["uid"], "edma_trace", entry)
        except Exception:
            pass
    return entry


def snapshot_summary(run: dict) -> dict:
    return {"run_id": run.get("id"), "state": run.get("state"), "steps": len(run.get("trace") or []),
            "dop_path": [t["state"] for t in (run.get("trace") or [])]}


def reconstruct(uid: str, run_id: str, run_doc: Optional[dict] = None) -> dict:
    """Rebuild the trajectory: in-run trace (snapshot) + durable events if available."""
    out = {"run_id": run_id, "trace": (run_doc or {}).get("trace") or [], "events": []}
    sink = ports.get_sink()
    if sink is not None:
        try:
            ev = sink.events_list(uid, "edma_trace", limit=200) or []
            out["events"] = [e for e in ev if (e.get("data") or {}).get("run_id") == run_id]
        except Exception:
            pass
    return out
