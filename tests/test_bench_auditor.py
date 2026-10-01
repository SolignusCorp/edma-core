"""§7 CONTRACT AUDITOR testlari — auditor MUSTAQIL spec nusxasi bilan."""


from edma_core.bench.auditor import AUDITOR_LEGAL, AUDITOR_STATES, audit_contract

CLEAN = {
    "mode": "edma", "model_calls": 4,
    "authorization": {"status": "granted"},
    "action_records": [{"type": "web.search", "executed": True, "ok": True}],
    "verification": {"verdict": "PASS", "evidence_present": True},
    "trace_events": [
        {"seq": 1, "state": "START", "decision": {"next": "PERCEIVING"}},
        {"seq": 2, "state": "PERCEIVING", "decision": {"next": "REASONING"}},
        {"seq": 3, "state": "REASONING", "decision": {"next": "ACTION_DECISION"}},
        {"seq": 4, "state": "ACTION_DECISION", "decision": {"next": "ACTION_AUTHORIZATION"}},
        {"seq": 5, "state": "ACTION_AUTHORIZATION", "decision": {"next": "ACTION"}},
        {"seq": 6, "state": "ACTION", "decision": {"next": "VERIFICATION"}},
        {"seq": 7, "state": "VERIFICATION", "decision": {"next": "RESPONSE"}},
        {"seq": 8, "state": "RESPONSE", "decision": {"next": "END"}},
        {"seq": 9, "state": "END", "decision": {"next": None}},
    ],
}


def test_clean_trace_no_violations():
    assert audit_contract({}, CLEAN) == []


def test_self_wait_is_not_illegal_transition():
    """CLARIFICATION_REQUIRED'da kutish — transition emas."""
    art = {"trace_events": [
        {"seq": 1, "state": "START", "decision": {"next": "PERCEIVING"}},
        {"seq": 2, "state": "PERCEIVING", "decision": {"next": "CLARIFICATION_REQUIRED"}},
        {"seq": 3, "state": "CLARIFICATION_REQUIRED", "decision": {"next": "CLARIFICATION_REQUIRED"}},
    ]}
    vs = audit_contract({}, art)
    assert not [v for v in vs if v["code"] == "dop_illegal_transition"]


def test_illegal_transition_detected():
    bad = dict(CLEAN)
    bad["trace_events"] = [
        {"seq": 1, "state": "START", "decision": {"next": "PERCEIVING"}},
        {"seq": 2, "state": "PERCEIVING", "decision": {"next": "ACTION"}},
        {"seq": 3, "state": "ACTION", "decision": {"next": "RESPONSE"}},
        {"seq": 4, "state": "RESPONSE", "decision": {"next": "END"}},
    ]
    codes = [v["code"] for v in audit_contract({}, bad)]
    assert codes.count("dop_illegal_transition") >= 2
    assert "action_without_authorization_state" in codes


def test_unknown_state_detected():
    bad = {"trace_events": [{"seq": 1, "state": "START", "decision": {"next": "MYSTERY_STATE"}},
                            {"seq": 2, "state": "MYSTERY_STATE", "decision": {"next": None}}]}
    codes = [v["code"] for v in audit_contract({}, bad)]
    assert "dop_unknown_state" in codes


def test_component_created_transition_flagged():
    """Component DOP'dan tashqari o'tish yaratishi — critical."""
    bad = {"trace_events": [
        {"seq": 1, "state": "START", "decision": {"next": "PERCEIVING"}},
        {"seq": 2, "state": "PERCEIVING", "decision": {"next": "RESPONSE"}},
    ]}
    codes = [v["code"] for v in audit_contract({}, bad)]
    assert "dop_illegal_transition" in codes


def test_action_without_granted_authorization():
    bad = dict(CLEAN)
    bad["authorization"] = {"status": "denied"}
    bad["op_authz_seen"] = False
    codes = [v["code"] for v in audit_contract({}, bad)]
    assert "action_without_granted_authorization" in codes


def test_verification_pass_without_evidence():
    bad = dict(CLEAN)
    bad["verification"] = {"verdict": "PASS", "evidence_present": False}
    codes = [v["code"] for v in audit_contract({}, bad)]
    assert "verification_pass_without_evidence" in codes


def test_model_control_fields_info_only():
    art = {"trace_events": [
        {"seq": 1, "state": "REASONING",
         "component": {"ignored_model_fields": {"authorized": True, "next_state": "END"}}}]}
    vs = audit_contract({}, art)
    assert vs and vs[0]["code"] == "model_control_fields_present"
    assert vs[0]["severity"] == "info"


def test_budget_exceeded():
    bad = dict(CLEAN)
    bad["model_calls"] = 99
    codes = [v["code"] for v in audit_contract({"max_model_calls": 12}, bad)]
    assert "budget_exceeded" in codes


def test_response_restart_detected():
    bad = dict(CLEAN)
    bad["trace_events"] = CLEAN["trace_events"][:-1] + [
        {"seq": 9, "state": "RESPONSE", "decision": {"next": "VERIFICATION"}},
        {"seq": 10, "state": "VERIFICATION", "decision": {"next": "END"}},
    ]
    codes = [v["code"] for v in audit_contract({}, bad)]
    assert "response_restarted_orchestration" in codes


def test_auditor_matrix_is_frozen_13_states():
    assert len(AUDITOR_STATES) == 13
    assert len(AUDITOR_LEGAL) == 24
