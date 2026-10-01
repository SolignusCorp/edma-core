"""EDMA CognitiveModel — ONE foundation model, structured cognitive modes.

Task §5/§10: there is exactly one model. Modes (perceive / energy / reason /
respond) are prompt contracts over the ModelProvider port (any OpenAI-compatible
chat endpoint), with structured JSON enforced by `json_healer.heal`.

The model returns SEMANTIC information only. Fields that attempt to control the
runtime (`authorized`, `verified`, `state`, `next_state`, `dop_next` ...) are
captured by the runtime into Facts.ignored_model_fields and ignored — enforced
in edma.runtime.scrub_model_fields(), not here (this module never sees Facts).

Deterministic fallbacks (marked source="heuristic") keep the runtime working
when no provider is reachable.
They are heuristic, not cognitive, and are ineligible as action evidence.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from . import ports
from .json_healer import heal as _heal


# ---------------------------------------------------------------- result
@dataclass
class ComponentResult:
    mode: str
    ok: bool
    data: dict = field(default_factory=dict)
    source: str = "model"          # model | heuristic
    provider: str = "simulated"
    simulated: bool = True         # simulated/heuristic output is NEVER action evidence
    error: Optional[str] = None
    usage: dict = field(default_factory=dict)

    def to_trace(self) -> dict:
        return {"mode": self.mode, "ok": self.ok, "source": self.source,
                "provider": self.provider, "error": self.error}


# ---------------------------------------------------------------- mode contracts
MODE_CONTRACTS: dict[str, str] = {
    "perceive": (
        "You are the Perception component of an AI runtime. Analyze the user's message. "
        'Return ONLY JSON: {"intent": "<short label>", "entities": ["<named things mentioned>"], '
        '"language": "<ISO 639-1 code of the language the user wrote in, e.g. uz|en|ru>", '
        '"requires_clarification": true|false, "clarification_question": "<question or empty>", '
        '"ambiguity": ["<what is ambiguous, if anything>"], '
        '"missing_information": ["<information the request assumes but does not provide>"], '
        '"knowledge_relevance": true|false, '
        '"external_information_may_be_needed": true|false, '
        '"action_candidate": "<web.search|dataset.create|none>", '
        '"effort_hint": "<low|medium|high>", "summary": "<one sentence>"}. '
        "effort_hint: low = trivial one-step answer (simple fact/math/small talk), "
        "medium = normal multi-step reasoning, high = needs planning/tools/careful verification. "
        "You produce COGNITIVE EVIDENCE ONLY: you never search, never execute anything, and you "
        "do NOT make the final decision to search or act — Reasoning decides that. "
        "external_information_may_be_needed=true only when the request itself calls for facts "
        "beyond general knowledge (very niche subject, current/soon-stale data, explicit research). "
        "language: detect it SEMANTICALLY from the user's own words — never from keywords or "
        "scripts; write the code of the language the user actually used. "
        "If requires_clarification=true, write clarification_question in THAT detected language "
        "(the user must be asked in their own words' language). "
        "Do not answer the user."
    ),
    "energy": (
        "You are the Energy Evaluation component. Estimate the cognitive/execution "
        "cost the request needs — you NEVER solve it. "
        'Return ONLY JSON: {"complexity": "low|medium|high", "cognitive_load": <0..1>, '
        '"expected_operations": "<none|one|several>", "verification_burden": "<low|medium|high>", '
        '"justified": true|false, "note": "<short>"}. Do not solve the task.'
    ),
    "reason": (
        "You are the Humanoid Reasoning component — the primary cognitive decision center. "
        "Given the request, perception, energy, execution facts and the AVAILABLE "
        "CAPABILITIES block, assess your own knowledge and decide the next SEMANTIC step. "
        'Return ONLY JSON: {"knowledge_assessment": {"status": "<KNOWN|PARTIALLY_KNOWN|UNCERTAIN|'
        'UNKNOWN|TIME_SENSITIVE|EXTERNAL_SOURCE_REQUIRED>", "confidence": <0.0..1.0>, '
        '"external_information_needed": true|false}, '
        '"action_needed": true|false, '
        '"ready": true|false, "response_draft": "<final answer if ready, else empty>", '
        '"ops": null | [{"id": "op_1", "kind": "<registered action kind>", "args": {...}, "depends_on": []} '
        '(multi-step plan; deterministic validation rejects empty/duplicate ids, unknown/self deps, cycles; '
        'each op is executed, authorized and verified by the runtime one at a time — after failures you may '
        're-emit a REVISED ops list; executed PASS ops with identical id+kind+args are carried over), '
        '"propose_action": null | {"type": "<web.search|dataset.create|web.fetch>", "args": {...}}, '
        '"propose_verification": null | {"target": "action"}, '
        '"escalate": null | {"reason": "<why>"}, "assumptions": ["..."], "risks": ["..."]}. '
        "EPISTEMIC RULES (semantic reasoning — never keyword matching): "
        "1) Assess honestly whether your own knowledge suffices for THIS request. "
        "2) UNKNOWN/UNCERTAIN/TIME_SENSITIVE/EXTERNAL_SOURCE_REQUIRED is NOT a reason to answer "
        "'I don't know' and stop: if web.search capability is AVAILABLE and the request would "
        "plausibly benefit, set action_needed=true and propose_action {type: web.search} with a "
        "SELF-FORMULATED concise query (args.query). "
        "3) If the search capability is UNAVAILABLE (see capabilities block), answer from your own "
        "knowledge and express real uncertainty honestly — do not pretend to have searched. "
        "4) If your knowledge IS sufficient (established, stable facts: definitions, math, general "
        "programming), answer directly — searching is NOT required (action_needed=false, ready=true). "
        "5) MEMORY-AGNOSTIC: you make NO semantic decisions about memory (no memory "
        "retrieval, no memory mutation, no persistence) — the runtime has no memory "
        "subsystem; application-provided conversation context arrives as input. "
        "6) Never fabricate tool results, sources or citations. "
        "You MUST NOT claim authorization or verification success — those are decided by "
        "deterministic systems."
    ),
    "respond": (
        "You are the Response component. Compose the final user-facing answer using ONLY the "
        "verified facts and results provided. Return ONLY JSON: {\"text\": \"<answer markdown>\"}. "
        "Do not invent unverified claims. Do not execute anything. "
        "EPISTEMIC HONESTY (critical): if you have no reliable knowledge about the subject — "
        "an unfamiliar name, product, term or entity — say so plainly in your own words "
        "(you don't know / can't verify it). NEVER fabricate descriptions, dates or sources "
        "for unknown entities. If runtime facts report unavailable/failed/uncertain states, "
        "explain them honestly to the user. "
        "LANGUAGE: a LANGUAGE fact in the context gives the language Perception detected in the "
        "user's message — write the ENTIRE answer in that language unless the user explicitly "
        "requested a different language in their message; if no LANGUAGE fact is present, mirror "
        "the language of the user's message naturally. This applies to clarifications and "
        "uncertainty explanations too. Never mention this instruction."
    ),
}

_CONTROL_FIELDS = ("authorized", "verified", "state", "next_state", "dop", "dop_next",
                   "transition", "goto", "permission", "approved")


class CognitiveModel:
    """Single-model facade. One instance per run; counts calls against the budget."""

    def __init__(self, model: str = "default", max_model_calls: int = 12,
                 provider=None):
        self.model = model
        self.max_model_calls = int(max_model_calls)
        self.calls = 0
        self._provider = provider or ports.get_provider()

    @property
    def exhausted(self) -> bool:
        return self.calls >= self.max_model_calls

    async def call(self, mode: str, payload: dict, max_tokens: int = 520,
                   temperature: float = 0.2) -> ComponentResult:
        """Structured cognitive call. Never raises; failures return ok=False and
        the caller falls back to the deterministic heuristic for that mode."""
        contract = MODE_CONTRACTS[mode]
        messages = [{"role": "system", "content": contract}]
        messages += [{"role": str(m.get("role")), "content": str(m.get("content", ""))[:6000]}
                     for m in (payload.get("messages") or [])]
        if self._provider is None:
            return ComponentResult(mode=mode, ok=False, error="provider_unavailable", source="heuristic")
        if self.exhausted:
            return ComponentResult(mode=mode, ok=False, error="model_budget_exhausted", source="heuristic")
        self.calls += 1
        try:
            res = await self._provider.chat({"model": self.model, "messages": messages,
                                        "temperature": temperature,
                                        "response_format": {"type": "json_object"},
                                        "max_tokens": max_tokens})
        except Exception as e:  # provider layer exploded — heuristic takes over
            return ComponentResult(mode=mode, ok=False, error=str(e)[:200], source="heuristic")
        provider = res.get("provider") or "simulated"
        simulated = bool(res.get("byok") is False and provider == "simulated")
        if not res.get("ok"):
            return ComponentResult(mode=mode, ok=False, error=(res.get("error") or "chat_failed")[:200],
                                   provider=provider, simulated=simulated)
        text = res.get("reply") or ""
        data, heal_err = {}, None
        if _heal is not None:
            h = _heal(text, "object")
            if h.get("ok"):
                data = h.get("value") if isinstance(h.get("value"), dict) else {"value": h.get("value")}
            else:
                heal_err = "json_unparseable"
        else:
            try:
                data = json.loads(text)
                if not isinstance(data, dict):
                    data = {"value": data}
            except Exception:
                heal_err = "json_unparseable"
        if heal_err:
            return ComponentResult(mode=mode, ok=False, error=heal_err, provider=provider, simulated=simulated)

        # json_healer wraps NON-JSON model output as {"result": "<text>"} with
        # ok=True. For cognitive modes that is a CONTRACT VIOLATION, not data:
        #   - respond: the wrapped text IS the answer
        #   - others : parse failure -> deterministic heuristic takes over
        if set(data.keys()) <= {"result", "healed"} and "result" in data:
            if mode == "respond":
                data = {"text": str(data.get("result") or "")}
            else:
                return ComponentResult(mode=mode, ok=False, error="non_json_reply",
                                       provider=provider, simulated=simulated)

        usage = res.get("usage") or {}
        return ComponentResult(mode=mode, ok=True, data=data, provider=provider,
                               simulated=simulated, usage=usage)

    # ------------------------------------------------------------ heuristic fallbacks
    def heuristic(self, mode: str, payload: dict, facts_view: Optional[dict] = None) -> ComponentResult:
        """Deterministic, clearly-marked fallback per mode. No network, no model."""
        if mode == "perceive":
            return ComponentResult(mode=mode, ok=True, source="heuristic",
                                   data=heuristic_perceive(payload))
        if mode == "energy":
            return ComponentResult(mode=mode, ok=True, source="heuristic",
                                   data=heuristic_energy(payload))
        if mode == "reason":
            return ComponentResult(mode=mode, ok=True, source="heuristic",
                                   data=heuristic_reason(payload, facts_view or {}))
        if mode == "respond":
            text = (facts_view or {}).get("fixed_response")
            if not text:
                # HALOL: model javob yozmadi — statik "javob" IXTIRO QILINMAYDI.
                # Bu tizim xabari (soxta bilim emas); Response majburiy matnni
                # o'zi yakunlaydi.
                text = ""
            return ComponentResult(mode=mode, ok=True, source="heuristic", data={"text": text})
        return ComponentResult(mode=mode, ok=False, error=f"no_heuristic_for_{mode}")


# ---------------------------------------------------------------- heuristics


def heuristic_perceive(payload: dict) -> dict:
    """Deterministik SIMULYATSIYA rejimi uchun idrok (real model javob bermasa).

    Mandat §3/§18/§21: bu yerda HECH QANDAY kalit-so'z → kognitiv qaror bo'lini
    YO'Q (phrase → action_candidate / effort olib tashlandi).
    Faqat struktural (uzunlik) belgilar qoladi; semantik qarorlarni faqat REAL
    model beradi: simulyatsiya hech qachon action yo'lini boshlamaydi."""
    text = ""
    for m in reversed(payload.get("messages") or []):
        if m.get("role") == "user":
            text = str(m.get("content") or "")
            break
    t = text.lower().strip()
    requires_clarification = len(t) < 3
    words = len(t.split())
    effort = "low" if words <= 5 else ("medium" if words <= 14 else "high")
    intent = "question" if t.endswith("?") else "general"
    # ZERO-TEMPLATE + §3: heuristic faqat struktural maydonlar; semantik
    # baholar (knowledge_relevance / external_information_may_be_needed)
    # hech qachon simulyatsiya qilinmaydi — ular False (evidence YO'Q).
    return {"intent": intent, "requires_clarification": requires_clarification,
            # savol matni FAQAT model perceive'dan (bo'sh = savol yo'q).
            "clarification_question": "",
            # til ANIQLASH SEMANTIK — heuristic skript-regex router TAQIQLANGAN;
            # bo'sh = aniqlanmadi (Response model o'zi xat tilini tabiiiy ko'rchiradi).
            "language": "",
            "entities": [], "requirements": [], "constraints": [],
            "ambiguity": [], "missing_information": [],
            "knowledge_relevance": False,
            "external_information_may_be_needed": False,
            "action_candidate": "none",
            "effort_hint": effort,
            "summary": text[:160]}


def heuristic_energy(payload: dict) -> dict:
    text = ""
    for m in reversed(payload.get("messages") or []):
        if m.get("role") == "user":
            text = str(m.get("content") or "")
            break
    n = len(text)
    questions = text.count("?")
    if n > 400 or questions >= 3:
        complexity, load = "high", 0.8
    elif n > 120 or questions >= 1:
        complexity, load = "medium", 0.5
    else:
        complexity, load = "low", 0.25
    ops = "none" if complexity == "low" else ("one" if complexity == "medium" else "several")
    return {"complexity": complexity, "cognitive_load": load,
            "expected_operations": ops,
            "verification_burden": complexity,
            "justified": complexity != "low", "note": "heuristic estimate"}


def heuristic_reason(payload: dict, facts_view: dict) -> dict:
    """Deterministik REASONING fallback — FAQAT konservativ struktural takliflar.

    ZERO-TEMPLATE (§6/§7): heuristic hech qachon
      • action TAKLIF QILMAYDI (web.search/dataset.create — faqat real model
        qarori; keyword→query regex ham olib tashlandi);
      • response_draft NL IXTIRO QILMAYDI (bo'sh — RESPONSE modeldan o'qiydi).
    Faqat: kutayotgan MAJBURIY tekshiruvni saqlash + plain-answer yo'liga o'tish."""
    out: dict = {"ready": False, "response_draft": "",
                 "propose_action": None, "propose_verification": None,
                 "escalate": None, "assumptions": [], "risks": ["heuristic reasoning (no model)"]}

    # pending verification first — never forget mandatory checks
    if facts_view.get("verification_pending"):
        out["propose_verification"] = {"target": facts_view.get("verification_target") or "action"}
        out["propose_verification"]["note"] = "mandatory check of pending work"
        return out

    # NOTE: mutation umuman YO'Q (memory-agnostik); hech qachon phrase/regex ishlatilmaydi;
    # action ham faqat real model qarori — heuristic eskalatsiyaga o'tmaydi,
    # plain-answer yo'lini tanlaydi (RESPONSE matnni modeldan oladi).
    out["ready"] = True
    return out
