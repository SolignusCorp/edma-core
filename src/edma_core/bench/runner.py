"""§9 RUNNER — bir case = CONTROL + EDMA (BIR XIL input), solishtirish, flaglar,
§12 defect report. §11 flag qoidalari kod bilan amalga oshirilgan."""
from __future__ import annotations

from edma_core.bench.adapters.mock import MockModelAdapter
from edma_core.bench.adapters.tools import ScriptedToolExecutor
from edma_core.bench.auditor import audit_contract
from edma_core.bench.control import run_control
from edma_core.bench.evaluator import evaluate_case
from edma_core.bench.metrics import aggregate, per_execution
from edma_core.bench.schemas import validate_case

VERDICT_SCORE = {"PASS": 1.0, "UNCERTAIN": 0.5, "FAIL": 0.0}


def _pack(case, artifact, evaluation, mode):
    return {
        "result": evaluation["verdict"],
        "score": VERDICT_SCORE.get(evaluation["verdict"], 0.0),
        "metrics": per_execution(case, artifact, evaluation),
        "evidence": {
            "final_text": str(artifact.get("final_text") or "")[:2000],
            "action_records": artifact.get("action_records") or [],
            "verification": artifact.get("verification"),
            "clarification_question": artifact.get("clarification_question") or "",
            "checks": evaluation.get("checks") or [],
            "escalation": artifact.get("escalation"),
            "run_id": artifact.get("run_id"),
            "dop_path": artifact.get("dop_path") or [],
            "approval_requested": bool(artifact.get("approval_requested")),
        },
        "mode": mode,
    }


def _flags(control, edma, violations, case) -> list:
    flags = []
    if edma["score"] < control["score"]:
        flags.append("edma_worse_than_control")
    if violations:
        flags.append("contract_violation")
    if control["result"] != "FAIL" and edma["result"] == "FAIL":
        flags.append("new_failure")
    mc, me = control["metrics"], edma["metrics"]
    if mc["verification_correctness"] and not me["verification_correctness"]:
        flags.append("verification_regression")
    if mc["action_correctness"] and not me["action_correctness"]:
        flags.append("action_regression")
    if me["hallucination"] and not mc["hallucination"]:
        flags.append("hallucination_regression")
    if control["score"] > 0 and me["latency"] > mc["latency"] * 10 + 5000:
        flags.append("latency_regression")
    if (me["input_tokens"] + me["output_tokens"]) > (mc["input_tokens"] + mc["output_tokens"]) * 4 + 2000:
        flags.append("token_regression")
    if me["error_count"] > 0:
        flags.append("unexpected_behavior")
    term = str((edma["evidence"].get("dop_path") or ["?"])[-1])
    if term not in ("RESPONSE", "END", "CLARIFICATION_REQUIRED"):
        flags.append("unexpected_behavior")
    return flags


def _defect_report(case, control, edma, violations, flags) -> dict | None:
    """§12 — faqat haqiqiy buzilish bo'lsa."""
    if not flags:
        return None
    v0 = violations[0] if violations else {}
    return {
        "report": "EDMA DEFECT DETECTED",
        "case_id": case["case_id"],
        "component": v0.get("component") or ("EDMA" if "edma_worse_than_control" in flags else "runtime"),
        "state": v0.get("state") or ((edma["evidence"].get("dop_path") or ["?"])[-1]),
        "expected": case.get("expected_behavior") or "",
        "actual": str(edma["evidence"].get("final_text") or "")[:600],
        "evidence": {"violations": violations[:8], "edma_checks": edma["evidence"]["checks"][:8],
                     "control_result": control["result"], "edma_result": edma["result"]},
        "contract_violated": v0.get("code") or ("; ".join(flags)[:120]),
        "severity": v0.get("severity") or ("major" if "edma_worse_than_control" in flags else "minor"),
        "reproducible": True,
        "control_result": control["result"],
        "edma_result": edma["result"],
    }


def run_case(case: dict, scripts: dict, adapter=None, edma_bridge=None) -> dict:
    """scripts: {control_turns:[...], control_tools:{...}, control_system: str?,
                 edma:{...}, search:[...]}  — har case uchun deterministik."""
    validate_case(case)
    adapter = adapter or MockModelAdapter(scripts.get("control_turns") or [])
    tools = ScriptedToolExecutor(scripts.get("control_tools") or {})

    control_art = run_control(case, adapter, tools,
                              system_prompt=scripts.get("control_system"))
    bridge = edma_bridge
    if bridge is None:
        from edma_core.bench.adapters.edma_binding import EdmaHarness
        bridge = EdmaHarness()
    edma_art = bridge.run(case, model_scripts=scripts.get("edma") or {},
                          search_script=scripts.get("search") or [])

    ev_c = evaluate_case(case, control_art)
    ev_e = evaluate_case(case, edma_art)
    violations = audit_contract(case, edma_art)

    control = _pack(case, control_art, ev_c, "control")
    edma = _pack(case, edma_art, ev_e, "edma")
    flags = _flags(control, edma, violations, case)

    regressions, improvements = [], []
    dims = ("correctness", "action", "verification", "recovery", "clarification", "safety")

    def dim_ok(pack, d):
        for c in pack["evidence"]["checks"]:
            if c.get("dim") == d and c.get("role") == "success":
                if str(c.get("status")).upper() == "VIOLATED":
                    return False
                if str(c.get("status")).upper() == "UNCERTAIN":
                    return None
        return True

    for d in dims:
        dc, de = dim_ok(control, d), dim_ok(edma, d)
        if dc is True and de is False:
            regressions.append(d)
        elif de is True and dc is not True:
            improvements.append(d)

    return {
        "case_id": case["case_id"],
        "category": case.get("category"),
        "control": control,
        "edma": edma,
        "comparison": {
            "delta_score": round(edma["score"] - control["score"], 3),
            "regressions": regressions,
            "improvements": improvements,
            "contract_violations": violations,
            "same_input": True,
        },
        "flags": flags,
        "defect": _defect_report(case, control, edma, violations, flags),
    }


def run_set(cases: list, scripts_by_case: dict, run_meta: dict,
            adapter=None, edma_bridge=None) -> dict:
    """Butun golden set: results + aggregate'lar (control/EDMA alohida) + reports."""
    results = []
    for case in cases:
        results.append(run_case(case, scripts_by_case.get(case["case_id"]) or {},
                                adapter=adapter, edma_bridge=edma_bridge))
    agg_c = aggregate([{"metrics": r["control"]["metrics"],
                        "first_pass": r["control"]["result"] == "PASS"} for r in results])
    agg_e = aggregate([{"metrics": r["edma"]["metrics"],
                        "first_pass": r["edma"]["result"] == "PASS"} for r in results])
    all_flags = [f for r in results for f in r["flags"]]
    return {
        "metadata": run_meta,
        "results": results,
        "control_aggregate": agg_c,
        "edma_aggregate": agg_e,
        "all_flags": sorted(set(all_flags)),
        "defects": [r["defect"] for r in results if r.get("defect")],
        "regression_cases": [r["case_id"] for r in results
                             if "edma_worse_than_control" in r["flags"]
                             or "new_failure" in r["flags"]],
    }
