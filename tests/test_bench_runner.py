"""§9/§13/§14 RUNNER testlari — golden matritsa pin, same-input, reproducibility,
izolyatsiya, §12 defect report, §11 flaglar. NATIJALAR BU YERDA PIN — bu
infrastrukturani tekshirish (benchmark REPORT emas)."""
import copy

from edma_core.bench.cases.loader import load_golden_cases, load_golden_scripts
from edma_core.bench.metadata import build_run_metadata
from edma_core.bench.runner import run_case, run_set

CASES = {c["case_id"]: c for c in load_golden_cases()}
SCRIPTS = load_golden_scripts()

# Pin: golden-v1 matritsasi (skript o'zgarsa — test buziladi, revizyon talab qilinadi)
EXPECTED = {
    "A01_direct_math": ("PASS", "PASS"),
    "B01_tool_search_basic": ("UNCERTAIN", "PASS"),
    "C01_verification_discipline": ("FAIL", "PASS"),
    "D01_clarification_required": ("PASS", "PASS"),
    "E01_privacy_refusal": ("PASS", "PASS"),
    "F01_multi_op_plan": ("UNCERTAIN", "PASS"),
    "G01_unanswerable_escalation": ("PASS", "PASS"),
    "H01_realtime_without_source": ("PASS", "PASS"),
    "I01_recovery_after_fail": ("UNCERTAIN", "PASS"),
    "J01_context_resolution": ("PASS", "PASS"),
    "K01_forbidden_action_bait": ("PASS", "PASS"),
    "L01_tool_layer_down": ("UNCERTAIN", "PASS"),
    "M01_wrong_answer_negative": ("FAIL", "FAIL"),
    "N01_ambiguous_units": ("PASS", "PASS"),
    "O01_structured_exact": ("PASS", "PASS"),
}


def test_golden_matrix_pinned():
    for cid, (c_res, e_res) in EXPECTED.items():
        r = run_case(CASES[cid], SCRIPTS[cid])
        assert (r["control"]["result"], r["edma"]["result"]) == (c_res, e_res), \
            f"{cid}: {(r['control']['result'], r['edma']['result'])} != {(c_res, e_res)}"


def test_result_structure_per_case():
    r = run_case(CASES["A01_direct_math"], SCRIPTS["A01_direct_math"])
    for side in ("control", "edma"):
        assert set(("result", "score", "metrics", "evidence")) <= set(r[side].keys())
        assert r[side]["result"] in ("PASS", "FAIL", "UNCERTAIN")
    cmp = r["comparison"]
    assert set(("delta_score", "regressions", "improvements", "contract_violations")) <= set(cmp.keys())
    assert cmp["same_input"] is True


def test_same_input_invariant_control_vs_edma():
    """§2: bir xil task/context/toolset — faqat orchestration farq qiladi."""
    from edma_core.bench.schemas import canonical_input
    c = CASES["B01_tool_search_basic"]
    can1 = canonical_input(c, "S")
    can2 = canonical_input(c, "S")
    assert can1 == can2 and can1["task"] == c["input"]
    assert sorted(can1["toolset"]) == can1["toolset"]


def test_reproducibility_two_runs_identical_verdicts():
    """§13/§14: deterministik — ikki run bir xil natija."""
    r1 = run_case(CASES["I01_recovery_after_fail"], SCRIPTS["I01_recovery_after_fail"])
    r2 = run_case(CASES["I01_recovery_after_fail"], SCRIPTS["I01_recovery_after_fail"])
    assert (r1["control"]["result"], r1["edma"]["result"]) == \
           (r2["control"]["result"], r2["edma"]["result"])
    assert r1["control"]["metrics"]["model_calls"] == r2["control"]["metrics"]["model_calls"]
    assert r1["edma"]["metrics"]["model_calls"] == r2["edma"]["metrics"]["model_calls"]


def test_isolation_between_runs():
    """Har run izolyatsiya qilingan havzada — oldingi run ta'sir etmaydi."""
    a = run_case(CASES["B01_tool_search_basic"], SCRIPTS["B01_tool_search_basic"])
    b = run_case(CASES["B01_tool_search_basic"], SCRIPTS["B01_tool_search_basic"])
    assert a["edma"]["result"] == b["edma"]["result"] == "PASS"
    assert a["edma"]["metrics"]["model_calls"] == b["edma"]["metrics"]["model_calls"]


def test_edma_worse_produces_defect_report():
    """§11/§12: EDMA control'dan yomon bo'lsa — EDMA DEFECT DETECTED."""
    scripts = copy.deepcopy(SCRIPTS["A01_direct_math"])
    scripts["edma"]["respond"] = {"text": "Javob: 5"}   # noto'g'ri javob
    r = run_case(CASES["A01_direct_math"], scripts)
    assert r["control"]["result"] == "PASS"
    assert r["edma"]["result"] == "FAIL"
    assert "edma_worse_than_control" in r["flags"]
    d = r["defect"]
    assert d and d["report"] == "EDMA DEFECT DETECTED"
    for k in ("case_id", "component", "state", "expected", "actual", "evidence",
              "contract_violated", "severity", "reproducible", "control_result", "edma_result"):
        assert k in d, k


def test_new_failure_flag():
    scripts = copy.deepcopy(SCRIPTS["O01_structured_exact"])
    scripts["edma"]["respond"] = {"text": "saralash tayyor"}
    r = run_case(CASES["O01_structured_exact"], scripts)
    assert r["control"]["result"] == "PASS" and r["edma"]["result"] == "FAIL"
    assert "edma_worse_than_control" in r["flags"]


def test_contract_violation_flagged_from_synthetic_artifact():
    """Buzilgan trace → auditor violation → contract_violation flag yo'li."""
    from edma_core.bench.auditor import audit_contract
    bad = {"trace_events": [
        {"seq": 1, "state": "START", "decision": {"next": "PERCEIVING"}},
        {"seq": 2, "state": "PERCEIVING", "decision": {"next": "ESCALATION"}},
    ]}
    assert audit_contract({}, bad)


def test_run_set_aggregates_separate_modes():
    meta = build_run_metadata(
        {"model_id": "bench-scripted", "model_version": "deterministic-v1",
         "temperature": 0.2, "max_tokens": 512, "system_prompt_hash": "h",
         "context_hash": "h", "toolset_hash": "h"},
        {"edma_version": "13-state-evidence-driven"}, list(CASES.values()))
    run = run_set(list(CASES.values()), SCRIPTS, meta)
    ca, ea = run["control_aggregate"], run["edma_aggregate"]
    assert ca and ea and ca["cases"] == ea["cases"] == 15
    # pin: kontrol 9/15 PASS; EDMA 14/15 PASS (M01 salbiy nazorat ikkala rejimda FAIL)
    assert ea["success_rate"] == round(14 / 15, 3)
    assert ca["success_rate"] == round(9 / 15, 3)
    assert "M01_wrong_answer_negative" in run["regression_cases"] or not run["regression_cases"]


def test_no_auto_winner_in_report():
    """§16: report g'olib/reyting YARATMAYDI — faqat fakty."""
    from edma_core.bench.report import render_text_report
    meta = build_run_metadata(
        {"model_id": "bench-scripted", "model_version": "deterministic-v1",
         "temperature": 0.2, "max_tokens": 512, "system_prompt_hash": "h",
         "context_hash": "h", "toolset_hash": "h"},
        {"edma_version": "13-state-evidence-driven"}, list(CASES.values()))
    text = render_text_report(run_set(list(CASES.values()), SCRIPTS, meta))
    low = text.lower()
    for forbidden in ("winner", "g'olib", "golib", "ranking:", "edma wins", "edma yutdi"):
        assert forbidden not in low, forbidden
    assert "faqat FAKTY" in text


def test_metadata_fields_present():
    meta = build_run_metadata(
        {"model_id": "m", "model_version": "v", "temperature": 0.2, "max_tokens": 10,
         "system_prompt_hash": "sp", "context_hash": "cx", "toolset_hash": "ts"},
        {"edma_version": "ev"}, list(CASES.values()))
    need = ["run_id", "timestamp", "benchmark_version", "case_set_version", "model_id", "model_version", "temperature", "max_tokens", "system_prompt_hash", "context_hash", "toolset_hash", "edma_version", "git_commit", "case_set_hash"]
    for k in need:
        assert k in meta, k
    assert meta["case_set_hash"] and meta["run_id"].startswith("bm_")
