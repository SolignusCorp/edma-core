"""§10 METRICS — har execution uchun kamida ko'rsatilgan set + aggregate'lar.
Control va EDMA ALOHIDA hisoblanadi. Evaluator natijasidan (hech kimning
da'vosidan emas) hisoblanadi."""
from __future__ import annotations


def per_execution(case: dict, artifact: dict, evaluation: dict) -> dict:
    checks = evaluation.get("checks") or []

    def dim_status(dim):
        st = [str(c.get("status") or "").upper() for c in checks
              if c.get("dim") == dim and c.get("role") == "success"]
        if not st:
            return None
        if any(s == "VIOLATED" for s in st):
            return False
        if all(s == "SATISFIED" for s in st):
            return True
        return None  # uncertain

    action_recs = artifact.get("action_records") or []
    executed = [a for a in action_recs if a.get("executed")]
    return {
        "task_success": evaluation.get("verdict") == "PASS",
        "correctness": dim_status("correctness"),
        "action_correctness": dim_status("action"),
        "verification_correctness": dim_status("verification"),
        "hallucination": dim_status("safety") is False,
        "unnecessary_action": any(a.get("unnecessary") for a in executed),
        "missed_action": dim_status("action") is False,
        "recovery_success": dim_status("recovery"),
        "clarification_correctness": dim_status("clarification"),
        "model_calls": int(artifact.get("model_calls") or 0),
        "input_tokens": int((artifact.get("tokens") or {}).get("input") or 0),
        "output_tokens": int((artifact.get("tokens") or {}).get("output") or 0),
        "latency": round(float(artifact.get("latency_ms") or 0), 1),
        "error_count": int(artifact.get("error_count") or 0),
    }


def _rate(vals: list) -> float:
    known = [v for v in vals if v is not None]
    if not known:
        return 0.0
    return round(sum(1 for v in known if v) / len(known), 3)


def aggregate(results: list) -> dict:
    """results: [{metrics: {...}, evaluation: {verdict}}...] — bir mode uchun."""
    if not results:
        return {}
    m = [r["metrics"] for r in results]
    first_pass = [r.get("first_pass", r["metrics"]["task_success"]) for r in results]
    return {
        "cases": len(results),
        "success_rate": _rate([x["task_success"] for x in m]),
        "first_pass_rate": _rate(first_pass),
        "verification_accuracy": _rate([x["verification_correctness"] for x in m]),
        "action_error_rate": round(1 - _rate([x["action_correctness"] for x in m
                                              if x["action_correctness"] is not None]), 3)
        if any(x["action_correctness"] is not None for x in m) else 0.0,
        "hallucination_rate": _rate([x["hallucination"] for x in m]),
        "recovery_rate": _rate([x["recovery_success"] for x in m
                                if x["recovery_success"] is not None]),
        "average_model_calls": round(sum(x["model_calls"] for x in m) / len(m), 2),
        "average_latency": round(sum(x["latency"] for x in m) / len(m), 1),
        "average_tokens": round(sum(x["input_tokens"] + x["output_tokens"] for x in m) / len(m), 1),
        "total_errors": sum(x["error_count"] for x in m),
    }
