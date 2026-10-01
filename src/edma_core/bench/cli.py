"""EDMA BENCH CLI — "run your own benchmark".

  python -m edma_core.bench list
  python -m edma_core.bench run --suite golden-v1 --limit 3
  python -m edma_core.bench run --suite golden-v1 --real          # env kalit kerak
  python -m edma_core.bench world --set world-v1 --real           # GSM8K/MMLU

Artifacts: ./bench_runs/<run_id>.json (gitignored). Kalitlar FAQAT env'dan;
real rejimda provayder javob bermasa — STOP (xato, jim davom etish YO'Q).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time


def _save_artifact(run: dict) -> str:
    d = "bench_runs"
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{(run.get('metadata') or {}).get('run_id', 'run')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(run, f, ensure_ascii=False, indent=1, default=str)
    return path


def cmd_list(_args) -> int:
    from .cases import loader
    print("Suites:")
    for name, info in loader.suite_info().items():
        print(f"  {name:12s} n={info['count']:3d}  {info['version']}  {info['description'][:70]}")
    print("\nReal rejim uchun env: EDMA_BENCH_KEYS (yoki EDMA_BENCH_KEY_N), "
          "EDMA_BENCH_BASE_URL, EDMA_BENCH_MODEL")
    return 0


def cmd_run(args) -> int:
    from . import metadata as _meta
    from .cases import loader
    from .report import render_text_report
    from .runner import run_set

    cases = loader.load_golden_cases()
    scripts = loader.load_golden_scripts()
    if args.limit:
        cases = cases[:args.limit]

    if args.real:
        try:
            from .adapters.openai_compat import OpenAICompatAdapter
            adapter = OpenAICompatAdapter()
        except RuntimeError as e:
            print(f"STOP: {e}", file=sys.stderr)
            return 2
        from .adapters.edma_binding import AdapterCognitiveModel
        bridge_model = AdapterCognitiveModel(adapter)  # xuddi shu adapter — ikkala rejim
        edma_bridge = None  # run_case ichida EdmaHarness(cognitive_model=...) yo'qi — pastda
        print(f"[real] adapter: {adapter.model_id} @ {adapter.base_url} "
              f"(kalitlar: {len(adapter.api_keys)})")
        results = []
        for case in cases:
            from .runner import run_case
            r = run_case(case, scripts.get(case["case_id"]) or {}, adapter=adapter,
                         edma_bridge=_RealBridge(adapter))
            results.append(r)
            print(f"  [{case['category']}] {case['case_id']}: "
                  f"control={r['control']['result']} edma={r['edma']['result']}")
        from .metrics import aggregate
        agg_c = aggregate([{"metrics": r["control"]["metrics"],
                            "first_pass": r["control"]["result"] == "PASS"} for r in results])
        agg_e = aggregate([{"metrics": r["edma"]["metrics"],
                            "first_pass": r["edma"]["result"] == "PASS"} for r in results])
        run = {
            "metadata": _meta.build_metadata(adapter=adapter, case_set="golden-v1",
                                             n=len(results), temperature=args.temperature),
            "results": results, "control_aggregate": agg_c, "edma_aggregate": agg_e,
            "all_flags": sorted({f for r in results for f in r["flags"]}),
            "defects": [r["defect"] for r in results if r.get("defect")],
            "regression_cases": [r["case_id"] for r in results
                                 if "edma_worse_than_control" in r["flags"]
                                 or "new_failure" in r["flags"]],
        }
    else:
        run = run_set(cases, scripts, _meta.build_metadata(case_set="golden-v1",
                                                           n=len(cases),
                                                           temperature=args.temperature))
        for r in run["results"]:
            print(f"  [{r['category']}] {r['case_id']}: "
                  f"control={r['control']['result']} edma={r['edma']['result']}")

    from .world import wilson
    ca, ea = run["control_aggregate"], run["edma_aggregate"]
    nc, ne = ca.get("cases", 0), ea.get("cases", 0)
    print()
    print(render_text_report(run))
    if nc and ne:
        print("\n== Wilson 95% CI (success rate) ==")
        print(f"control: {ca['success_rate']:.3f} CI{list(wilson(ca['success_rate'], nc))} (n={nc})")
        print(f"edma   : {ea['success_rate']:.3f} CI{list(wilson(ea['success_rate'], ne))} (n={ne})")
        print("(CI'lar kesishishi kutilgan — ustunlik da'vosI QILINMAYDI)")
    path = _save_artifact(run)
    print(f"\nartifact: {path}")
    return 0


class _RealBridge:
    """Real adapter'ni EDMA CognitiveModel ko'prigi orqali ishlatuvchi bridge."""

    def __init__(self, adapter):
        from .adapters.edma_binding import AdapterCognitiveModel, EdmaHarness
        self._h = EdmaHarness()
        self._cog = AdapterCognitiveModel(adapter)

    def run(self, case, model_scripts=None, search_script=None, temperature=0.2):
        return self._h.run(case, cognitive_model=self._cog,
                           search_script=search_script or [])


def cmd_world(args) -> int:
    from .world import run_world_set
    if args.real:
        try:
            from .adapters.openai_compat import OpenAICompatAdapter
            adapter = OpenAICompatAdapter(temperature=0.0)
        except RuntimeError as e:
            print(f"STOP: {e}", file=sys.stderr)
            return 2
        print(f"[real] adapter: {adapter.model_id}")
        out = run_world_set(adapter, name=args.set, limit=args.limit, mode_name=adapter.model_id)
    else:
        from .adapters.mock import MockModelAdapter
        adapter = MockModelAdapter(["#### 42", "B", "#### 7", "A"])
        out = run_world_set(adapter, name=args.set, limit=args.limit, mode_name="mock")
    print(f"\n{out['set']} [{out['mode']}]: {out['correct']}/{out['n']} = {out['rate']:.1%} "
          f"CI95{out['ci95']}")
    os.makedirs("bench_runs", exist_ok=True)
    path = os.path.join("bench_runs", f"world_{args.set}_{int(time.time())}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("artifact:", path)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="edma_core.bench",
                                description="EDMA benchmark lab (mustaqil evaluator)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="suite'lar ro'yxati").set_defaults(func=cmd_list)
    pr = sub.add_parser("run", help="golden set: CONTROL vs EDMA")
    pr.add_argument("--suite", default="golden-v1")
    pr.add_argument("--limit", type=int, default=0)
    pr.add_argument("--real", action="store_true", help="env kalit bilan real model")
    pr.add_argument("--temperature", type=float, default=0.2)
    pr.set_defaults(func=cmd_run)
    pw = sub.add_parser("world", help="world set (GSM8K/MMLU) deterministik grading")
    pw.add_argument("--set", default="world-v1", choices=["world-v1", "world-v2"])
    pw.add_argument("--limit", type=int, default=0)
    pw.add_argument("--real", action="store_true")
    pw.set_defaults(func=cmd_world)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
