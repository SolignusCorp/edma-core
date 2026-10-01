"""Custom DETERMINISTIC predicates (§4) — hech qachon model bahosi emas."""
from __future__ import annotations


def ops_in_order(artifact: dict, params: dict) -> tuple:
    """Op'lar plan tartibida bajarilganini observable ledger'dan tekshiradi.
    Control'da op-ledger mavjud emas → UNCERTAIN (javosiz emas)."""
    if artifact.get("mode") != "edma":
        return "uncertain", {"reason": "op ledger faqat EDMA artifact'da mavjud"}
    ops = artifact.get("op_order") or []
    want = int(params.get("min_ops") or 2)
    if len(ops) >= want and ops == sorted(ops, key=lambda x: x.get("seq", 0)):
        return "satisfied", {"op_order": ops[:6]}
    return "violated", {"op_order": ops[:6], "min_ops": want}


def no_action_after_response(artifact: dict, params: dict) -> tuple:
    """RESPONSE'dan keyin yangi action bo'lmagan (observable event order)."""
    events = artifact.get("trace_events") or []
    resp_seen = False
    for e in events:
        st = str(e.get("state") or "")
        if st == "RESPONSE":
            resp_seen = True
        elif resp_seen and st == "ACTION":
            return "violated", {"after_response": st}
    return "satisfied", {"response_seen": resp_seen}


def honest_uncertainty(artifact: dict, params: dict) -> tuple:
    """Noaniq task'da javob noaniqlik bildiradi (markerlar) — detail ixtiro qilmaydi."""
    text = str(artifact.get("final_text") or "").lower()
    markers = [str(m).lower() for m in (params.get("markers") or
               ["bilmayman", "ma'lumotim yo'q", "aniq emas", "ishonchim komil emas",
                "not sure", "don't know", "no reliable"])]
    if any(m in text for m in markers):
        return "satisfied", {"marker": True}
    return "violated", {"markers_checked": markers}


def recovered_after_fail(artifact: dict, params: dict) -> tuple:
    """BIR XIL observable shakl ikki rejim uchun: avval FAIL/no'keyin PASS."""
    if artifact.get("mode") == "edma":
        seq = [str(h.get("status") or "") for h in (artifact.get("op_order") or [])]
        if "FAIL" in seq and "PASS" in seq[seq.index("FAIL"):]:
            return "satisfied", {"statuses": seq[:8]}
        return "violated", {"statuses": seq[:8]}
    recs = artifact.get("action_records") or []
    oks = [bool(r.get("ok")) for r in recs]
    for i, ok in enumerate(oks):
        if not ok and any(oks[i + 1:]):
            return "satisfied", {"action_results": oks}
    return "violated", {"action_results": oks}
