"""§10 METRICS testlari — per-execution 14 maydon + aggregate."""


from edma_core.bench.metrics import aggregate, per_execution

ART = {"final_text": "8", "model_calls": 3, "tokens": {"input": 30, "output": 12},
       "latency_ms": 120.5, "error_count": 0, "action_records": []}
EVAL = {"verdict": "PASS", "checks": [
    {"dim": "correctness", "role": "success", "status": "satisfied"},
    {"dim": "verification", "role": "success", "status": "uncertain"},
]}


def test_per_execution_14_fields():
    m = per_execution({}, ART, EVAL)
    need = ["task_success", "correctness", "action_correctness", "verification_correctness", "hallucination", "unnecessary_action", "missed_action", "recovery_success", "clarification_correctness", "model_calls", "input_tokens", "output_tokens", "latency", "error_count"]
    assert set(need) <= set(m.keys())
    assert m["task_success"] is True
    assert m["correctness"] is True
    assert m["verification_correctness"] is None   # uncertain — hech qachon True/False deb qaralmaydi
    assert m["model_calls"] == 3 and m["input_tokens"] == 30 and m["output_tokens"] == 12
    assert m["latency"] == 120.5 and m["error_count"] == 0


def test_hallucination_true_on_violated_safety():
    e = {"verdict": "FAIL", "checks": [{"dim": "safety", "role": "success", "status": "violated"}]}
    assert per_execution({}, ART, e)["hallucination"] is True


def test_aggregate_rates():
    rs = []
    for verdict, corr in (("PASS", True), ("PASS", True), ("FAIL", False)):
        checks = [{"dim": "correctness", "role": "success",
                   "status": "satisfied" if corr else "violated"}]
        rs.append({"metrics": per_execution({}, ART, {"verdict": verdict, "checks": checks}),
                   "first_pass": verdict == "PASS"})
    agg = aggregate(rs)
    assert agg["cases"] == 3
    assert agg["success_rate"] == round(2 / 3, 3)
    assert agg["first_pass_rate"] == round(2 / 3, 3)
    assert agg["average_model_calls"] == 3.0
    assert agg["total_errors"] == 0


def test_aggregate_empty_is_empty_dict():
    assert aggregate([]) == {}
