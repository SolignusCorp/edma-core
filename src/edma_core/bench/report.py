"""§16 REPORT FORMAT — fakty, delta, defect, regression, UNCERTAIN.
UMUMAN AVTOMATIC G'OLIB/REYTING YARATILMAYDI (§15/§16)."""
from __future__ import annotations


def _pct(x):
    return f"{round(100 * float(x or 0), 1)}%"


def render_text_report(run: dict) -> str:
    m = run.get("metadata") or {}
    ca, ea = run.get("control_aggregate") or {}, run.get("edma_aggregate") or {}
    L = []
    L.append("EDMA BENCHMARK — RUN REPORT")
    L.append(f"run: {m.get('run_id')} | {m.get('timestamp_iso') or ''}")
    L.append(f"model: {m.get('model_id')} {m.get('model_version') or ''} "
             f"(temperature={m.get('temperature')}, max_tokens={m.get('max_tokens')})")
    L.append(f"EDMA version: {m.get('edma_version')} | git: {m.get('git_commit')} "
             f"| benchmark: {m.get('benchmark_version')} | cases: {m.get('case_set_version')} "
             f"({m.get('case_set_hash')})")
    L.append(f"hashes: prompt={m.get('system_prompt_hash')} ctx={m.get('context_hash')} "
             f"tools={m.get('toolset_hash')}")
    L.append("")
    L.append("== CONTROL ==")
    L.append(f"success {_pct(ca.get('success_rate'))} | first-pass {_pct(ca.get('first_pass_rate'))} "
             f"| verification {_pct(ca.get('verification_accuracy'))} | action-err {_pct(ca.get('action_error_rate'))} "
             f"| hallucination {_pct(ca.get('hallucination_rate'))}")
    L.append(f"avg calls {ca.get('average_model_calls')} | latency {ca.get('average_latency')}ms "
             f"| tokens {ca.get('average_tokens')} | errors {ca.get('total_errors')}")
    L.append("")
    L.append("== EDMA ==")
    L.append(f"success {_pct(ea.get('success_rate'))} | first-pass {_pct(ea.get('first_pass_rate'))} "
             f"| verification {_pct(ea.get('verification_accuracy'))} | action-err {_pct(ea.get('action_error_rate'))} "
             f"| hallucination {_pct(ea.get('hallucination_rate'))}")
    L.append(f"avg calls {ea.get('average_model_calls')} | latency {ea.get('average_latency')}ms "
             f"| tokens {ea.get('average_tokens')} | errors {ea.get('total_errors')}")
    L.append("")
    L.append("== DIFFERENCES (case-by-case, faqat FAKTY — umumiy hukm/reyting chiqarilmaydi) ==")
    for r in run.get("results") or []:
        c, e, cmp = r["control"], r["edma"], r["comparison"]
        L.append(f"[{r.get('category')}] {r['case_id']}: control={c['result']} edma={e['result']} "
                 f"delta={cmp['delta_score']:+.2f} flags={','.join(r['flags']) or '-'}")
    L.append("")
    L.append("== DEFECTS (EDMA DEFECT DETECTED) ==")
    defects = run.get("defects") or []
    if not defects:
        L.append("(yo'q)")
    for d in defects:
        L.append(f"- {d['case_id']} [{d['component']}@{d['state']}] {d['contract_violated']} "
                 f"({d['severity']}) expected: {str(d['expected'])[:120]}")
    L.append("")
    L.append("== REGRESSIONS ==")
    regs = run.get("regression_cases") or []
    L.append("; ".join(regs) if regs else "(yo'q)")
    L.append("")
    L.append("== UNCERTAIN CASES (hech qachon PASS'ga ko'tarilmaydi) ==")
    unc = [r["case_id"] for r in (run.get("results") or [])
           if r["control"]["result"] == "UNCERTAIN" or r["edma"]["result"] == "UNCERTAIN"]
    L.append("; ".join(unc) if unc else "(yo'q)")
    return "\n".join(L)
