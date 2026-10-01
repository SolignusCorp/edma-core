"""EDMA Verification — evidence-based, deterministic where evidence permits.

Task §18: verdicts are PASS | FAIL | UNCERTAIN | SKIPPED. SKIPPED is NOT PASS.
Never trusted: verified=true, success=true, model confidence, model assertions,
bare function returns. Actual effects are checked against STORAGE (read-back),
not against claims.

Actions: evidence rules per action type; simulated/mock provider output is
ineligible as evidence (heuristic/simulated → UNCERTAIN, never PASS).

The rules are CONTRACTS about result/evidence dicts — pure and portable. The
only environment touch is dataset read-back (DatasetReader port).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from . import ports as _ports

# Test seam + override: dataset read-back reader (DatasetReader port).
# When None, the port registry is consulted; if still None -> UNCERTAIN (honest).
_store = None

PASS, FAIL, UNCERTAIN, SKIPPED = "PASS", "FAIL", "UNCERTAIN", "SKIPPED"

from .dop import VERIFICATION as _V  # noqa: F401  (state name lives in dop only)


@dataclass
class Verdict:
    result: str                       # PASS | FAIL | UNCERTAIN | SKIPPED
    target: str                       # action
    mandatory: bool
    reason: str
    evidence_summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"verdict": self.result, "target": self.target, "mandatory": self.mandatory,
                "reason": self.reason, "evidence_summary": self.evidence_summary}


# ---------------------------------------------------------------- actions
def verify_action(action: str, result: dict, uid: str = "") -> Verdict:
    """Deterministic evidence rules per registered action.

    `result` is the ActionResult dict produced by edma.actions.execute —
    NOT anything the model claims.
    """
    if action == "web.search":
        # §10: "natija keldi" ≠ PASS. Query + qaytgan dalillarni deterministik
        # baholash: real provayder, http URL, BO'SH BO'LMAGAN kontent va
        # query→evidence bog'liqlik (lexical sanity — kognitsiya EMAS).
        # Semantik ishonchni Response komponenti (model) ifodalaydi.
        ev = result.get("evidence") or {}
        results = ev.get("results") or []
        provider = ev.get("provider") or result.get("output", {}).get("provider")
        query = str(ev.get("query") or result.get("output", {}).get("query") or "")
        if result.get("error") == "search_engine_unavailable":
            return Verdict(UNCERTAIN, "action", True, "search layer unavailable", {})
        if provider in ("mock", None):
            # simulated search results are not real-world evidence
            return Verdict(UNCERTAIN, "action", True,
                           "search provider simulated/mock — results are not confirmed evidence",
                           {"provider": provider, "results_count": len(results)})
        usable = [r for r in results
                  if str(r.get("url", "")).startswith("http")
                  and str(r.get("content") or r.get("snippet") or "").strip()]  # title YALG'IZ yetarli emas
        if not results:
            return Verdict(FAIL, "action", True, "no search results retrieved",
                           {"provider": provider, "query": query[:120]})
        if not usable:
            return Verdict(UNCERTAIN, "action", True,
                           "results lack usable content evidence",
                           {"provider": provider, "results_count": len(results), "query": query[:120]})
        # SEMANTIK MOSLIK (dalil ↔ savol) — deterministik emas, KOGNITIV:
        # bu ikkinchi REASONING pass'ida model tomonidan baholanadi
        # (VERIFICATION UNCERTAIN → REASONING → yanada aniq query yoki
        # halol noaniqlik). Bu yerdagi lexical regex mo'rt bo'lardi —
        # kognitsiyani regexga topshirish taqiqlangan (§2).
        claims = [{"source_ref": str(r.get("url", ""))[:160],
                   "claim": str(r.get("title", ""))[:120] or str(r.get("content", ""))[:120],
                   "excerpt": str(r.get("content") or r.get("snippet") or "")[:200]}
                  for r in usable[:5]]
        return Verdict(PASS, "action", True,
                       f"{len(usable)} usable search results with content evidence",
                       {"provider": provider, "results_count": len(usable),
                        "query": query[:120], "evidence": claims})

    if action == "dataset.create":
        ds_id = (result.get("evidence") or {}).get("dataset_id") or result.get("output", {}).get("dataset_id")
        if not result.get("ok") or not ds_id:
            return Verdict(FAIL, "action", True, f"dataset creation failed: {result.get('error')}", {})
        expected_n = (result.get("evidence") or {}).get("samples")
        reader = _store or _ports.get_dataset_reader()
        if reader is None:
            return Verdict(UNCERTAIN, "action", True, "dataset reader unavailable — cannot read back dataset", {})
        rec = reader.get_dataset(str(ds_id), uid)
        if rec is None:
            return Verdict(FAIL, "action", True, "dataset absent after create (read-back)", {"dataset_id": ds_id})
        got_n = len(rec.get("examples") or [])
        if expected_n and got_n != expected_n:
            return Verdict(FAIL, "action", True, "sample count mismatch after read-back",
                           {"dataset_id": ds_id, "expected": expected_n, "actual": got_n})
        return Verdict(PASS, "action", True, "dataset read-back matches",
                       {"dataset_id": ds_id, "samples": got_n})

    if action == "web.fetch":
        ev = result.get("evidence") or {}
        url = str(ev.get("url") or result.get("output", {}).get("url") or "")
        sha = str(ev.get("sha16") or "")
        chars = int(ev.get("chars") or 0)
        if not result.get("ok"):
            return Verdict(FAIL, "action", True,
                           f"web.fetch failed: {result.get('error')}", {"url": url[:120]})
        if not url.startswith("https://") or not sha or chars <= 0:
            return Verdict(UNCERTAIN, "action", True,
                           "web.fetch evidence incomplete (url/sha/chars)", ev)
        return Verdict(PASS, "action", True,
                       f"fetched {chars} chars with content hash evidence", 
                       {"url": url[:120], "sha16": sha, "chars": chars})


    return Verdict(UNCERTAIN, "action", True, f"no verification rule for action '{action}' — not assumed PASS", {})
