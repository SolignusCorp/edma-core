"""§7 EDMA CONTRACT AUDITOR — contract VIOLATION ovchisi (hakem emas).

AUDITOR o'zining MUSTAQIL spec nusxasi bilan ishlaydi (13-state DOP + qonuniy
o'tishlar matrixi bu yerda QAYTA YOZILGAN — direktiv matnidan). EDMA kodini
import qilmaydi; agar EDMA drift qilsa — auditor flag qiladi.
"""
from __future__ import annotations

# ── MUSTAQIL SPEC NUSXASI (direktiv §15; edma.dop O'QILMAYDI) ──
AUDITOR_STATES = {
    "START", "PERCEIVING", "ENERGY_EVALUATION", "REASONING", "VERIFICATION",
    "ACTION_DECISION", "ACTION_AUTHORIZATION", "ACTION", "ESCALATION_CHECK",
    "ESCALATION", "CLARIFICATION_REQUIRED", "RESPONSE", "END"}

AUDITOR_LEGAL = {
    ("START", "PERCEIVING"),
    ("PERCEIVING", "ENERGY_EVALUATION"), ("PERCEIVING", "REASONING"),
    ("PERCEIVING", "CLARIFICATION_REQUIRED"),
    ("ENERGY_EVALUATION", "REASONING"),
    ("REASONING", "VERIFICATION"), ("REASONING", "ACTION_DECISION"),
    ("REASONING", "ESCALATION_CHECK"), ("REASONING", "RESPONSE"),
    ("VERIFICATION", "REASONING"), ("VERIFICATION", "ACTION_DECISION"),
    ("VERIFICATION", "ESCALATION_CHECK"), ("VERIFICATION", "RESPONSE"),
    ("ACTION_DECISION", "ACTION_AUTHORIZATION"),
    ("ACTION_AUTHORIZATION", "ACTION"), ("ACTION_AUTHORIZATION", "ESCALATION_CHECK"),
    ("ACTION_AUTHORIZATION", "REASONING"),
    ("ACTION", "VERIFICATION"),
    ("ESCALATION_CHECK", "ESCALATION"), ("ESCALATION_CHECK", "REASONING"),
    ("ESCALATION_CHECK", "RESPONSE"),
    ("ESCALATION", "RESPONSE"),
    ("CLARIFICATION_REQUIRED", "RESPONSE"),
    ("RESPONSE", "END"),
}

_CONTROL_FIELDS = ("authorized", "approved", "verified", "verification_passed",
                   "state", "next_state", "dop", "dop_next", "goto", "transition",
                   "permission", "grant", "authorize")


def audit_contract(case: dict, artifact: dict) -> list:
    """EDMA trace artifact'ini contract buzilishlariga tekshiradi.
    Har violation: {code, component, state, detail, severity, evidence}."""
    violations = []
    events = artifact.get("trace_events") or []

    def add(code, component, state, detail, severity, evidence=None):
        violations.append({"code": code, "component": component, "state": state,
                           "detail": detail, "severity": severity,
                           "evidence": evidence or {}})

    # 1) unknown / component-created states
    for e in events:
        st = str(e.get("state") or "")
        if st and st not in AUDITOR_STATES:
            add("dop_unknown_state", "?", st, "trace'da spec'dan tashqari state", "critical",
                {"event": e.get("seq")})

    # 2) illegal transitions (component route YO'Q bo'lishi kerak)
    prev = None
    for e in events:
        st, nxt = e.get("state"), (e.get("decision") or {}).get("next")
        if prev and nxt and nxt != prev:  # self-wait (klarifikatsiya kutishi) — transition EMAS
            if (prev, nxt) not in AUDITOR_LEGAL:
                add("dop_illegal_transition", "?", str(prev),
                    f"{prev} → {nxt} spec matrixida YO'Q", "critical", {"seq": e.get("seq")})
        prev = nxt or prev

    # 3) ACTION — oldingi ACTION_AUTHORIZATION'siz bo'lmasin
    seen_authz = False
    for e in events:
        st = str(e.get("state") or "")
        if st == "ACTION_AUTHORIZATION":
            seen_authz = True
        if st == "ACTION" and not seen_authz:
            add("action_without_authorization_state", "Action", "ACTION",
                "ACTION authorization state'siz bajarilgan", "critical", {"seq": e.get("seq")})

    # 3b) executed action, granted authz record'siz (observable)
    authz = artifact.get("authorization") or {}
    executed = [a for a in (artifact.get("action_records") or []) if a.get("executed")]
    if executed and str(authz.get("status") or "") not in ("granted", "human_approved", "auto"):
        if not artifact.get("op_authz_seen"):
            add("action_without_granted_authorization", "Authorization", "ACTION_AUTHORIZATION",
                "bajarilgan action uchun granted authz yozuvi YO'Q", "critical",
                {"authz": authz, "executed": len(executed)})

    # 4) verification PASS evidence'siz (NO EVIDENCE -> NO PASS)
    ver = artifact.get("verification") or {}
    if str(ver.get("verdict") or "").upper() == "PASS" and not ver.get("evidence_present"):
        add("verification_pass_without_evidence", "Verification", "VERIFICATION",
            "PASS e'lon qilingan, lekin evidence YO'Q", "critical", {"verdict": ver})

    # 5) RESPONSE'dan keyin yangi orchestration (response restart qilmaydi)
    resp_seen = False
    for e in events:
        st = str(e.get("state") or "")
        if st == "RESPONSE":
            if resp_seen:
                add("response_repeated", "Response", "RESPONSE",
                    "RESPONSE state takrorlangan", "major", {"seq": e.get("seq")})
            resp_seen = True
        elif resp_seen and st not in ("END",):
            add("response_restarted_orchestration", "Response", st,
                f"RESPONSE'dan KEYIN yana orchestration: {st}", "critical", {"seq": e.get("seq")})

    # 6) ESCALATION — ESCALATION_CHECK'siz sakramaslik
    seen_check = False
    for e in events:
        st = str(e.get("state") or "")
        if st == "ESCALATION_CHECK":
            seen_check = True
        if st == "ESCALATION" and not seen_check:
            add("escalation_without_check", "Escalation", "ESCALATION",
                "component ESCALATION_CHECK'siz ESCALATION'ga sakragan", "major",
                {"seq": e.get("seq")})

    # 7) model control fieldlari ishlatilganmi (INFO — correlation uchun)
    for e in events:
        ign = (e.get("component") or {}).get("ignored_model_fields") or {}
        used = [k for k in ign if str(k).lower() in _CONTROL_FIELDS]
        if used:
            add("model_control_fields_present", "?", str(e.get("state")),
                "model control fieldlari yuborgan (ignore qilingan) — routing'ga ta'sir etmagan",
                "info", {"fields": sorted(set(used))[:6], "seq": e.get("seq")})

    # 8) budget
    mm = int(case.get("max_model_calls") or 99)
    if int(artifact.get("model_calls") or 0) > mm:
        add("budget_exceeded", "?", "?",
            f"model_calls {artifact.get('model_calls')} > max {mm}", "major", {})

    return violations


def contract_violation_summary(violations: list) -> str:
    crit = [v for v in violations if v["severity"] in ("critical", "major")]
    if not crit:
        return "no contract violations"
    return "; ".join(f"{v['code']}@{v['state']}" for v in crit[:6])
