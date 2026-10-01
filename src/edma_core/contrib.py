"""EDMA-CORE CONTRIBUT — capability action factories (plugins, core emas).

Bu modul IXTIYORIY: hech kim uni import qilmaydi, faqat foydalanuvchi o'zi
registry'ga qo'shadi. Har factory TASHQI DEPENDENSIYALARNI INJEKTSIYA qiladi —
core hech qachon store/wallet/search modullarini bilmaydi.

Misollar:
  from edma_core import actions, contrib
  actions.register(contrib.make_web_search_spec(my_search_fn, spend_fn=my_spend))
  actions.register(contrib.make_dataset_create_spec(my_generate_fn))
"""
from __future__ import annotations

from typing import Callable, Optional

from .actions import ActionSpec, SEARCH_COST, ALLOWED_LANGS


def make_web_search_spec(search_fn: Callable[[str, int], dict],
                         spend_fn: Optional[Callable[[str, float, str, dict], None]] = None,
                         cost: float = SEARCH_COST) -> ActionSpec:
    """web.search capability.

    search_fn(query, max_results) -> {'provider': str, 'results': [
        {'url': str, 'title': str, 'content'|'snippet': str}, ...]
    spend_fn(uid, cost, reason, meta) — IXTIYORIY metering hook (raise = rad).
    """
    def _h(uid: str, args: dict, ctx: dict) -> dict:
        try:
            if spend_fn is not None:
                spend_fn(uid, cost, "edma_web_search",
                         {"query": str(args.get("query", ""))[:160], "run_id": ctx.get("run_id")})
        except Exception as e:
            return {"ok": False, "error": f"spend_refused: {str(e)[:100]}"}
        res = search_fn(str(args.get("query", "")), int(args.get("max_results", 5)))
        results = res.get("results") or []
        return {"ok": bool(results), "provider": res.get("provider"),
                "results": results,
                "evidence": {"provider": res.get("provider"), "results": results,
                             "query": str(args.get("query", ""))[:200]}}
    return ActionSpec(
        name="web.search",
        description="Live web search via an injected search function (optionally metered).",
        schema={"query": (str, 1, 300, True), "max_results": (int, 1, 10, False)},
        risk="medium", requires_human=False, cost_estimate=cost, handler=_h)


def make_dataset_create_spec(generate_fn: Callable[..., list],
                             add_fn: Optional[Callable[..., Optional[dict]]] = None,
                             max_samples: int = 50) -> ActionSpec:
    """dataset.create capability (HIGH RISK → human approval gate).

    generate_fn(uid, topic, size, languages) -> [{'messages': [...]}, ...]
      — samples FAQAT real model yozuvidan; hech qanday template YO'Q.
    add_fn(rec_meta, examples) -> {'id': ...} | None — IXTIYORIY storage.
    """
    def _h(uid: str, args: dict, ctx: dict) -> dict:
        size = int(args.get("size", 30))
        if size > max_samples:
            return {"ok": False, "error": f"cap: max {max_samples} samples"}
        language = args.get("language", "en")
        language = language if language in ALLOWED_LANGS else "en"
        topic = str(args.get("topic", "")).strip()[:80]
        if not topic:
            return {"ok": False, "error": "topic_required"}
        try:
            examples = generate_fn(uid, topic, max(1, min(size, max_samples, 24)), [language])
        except Exception as e:
            return {"ok": False, "error": f"execution_failed: {str(e)[:120]}"}
        if not examples:
            return {"ok": False, "error": "model_unavailable",
                    "evidence": {"reason": "no samples passed QC; nothing fabricated"}}
        if add_fn is None:
            return {"ok": True, "dataset_id": "ephemeral", "samples": len(examples),
                    "evidence": {"dataset_id": "ephemeral", "samples": len(examples), "topic": topic}}
        rec = add_fn({"name": (topic[:40] + " dataset"), "topic": topic,
                      "size": len(examples), "language": language,
                      "format": "chat-jsonl"}, examples)
        if rec is None:
            return {"ok": False, "error": "storage_unavailable"}
        return {"ok": True, "dataset_id": rec["id"], "samples": len(examples),
                "evidence": {"dataset_id": rec["id"], "samples": len(examples), "topic": topic}}
    return ActionSpec(
        name="dataset.create",
        description="Generate a chat-jsonl dataset (cap-capped, human-approved).",
        schema={"topic": (str, 1, 80, True), "size": (int, 10, 2000, False),
                "language": (str, 2, 2, False)},
        risk="high", requires_human=True, cost_estimate=0.02, handler=_h)
