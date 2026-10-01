"""EDMA Runtime — resumable DOP loop around ONE foundation model.

Authority rules enforced here (task §4/§22):
  - the ONLY caller of edma.dop.decide() in the entire package;
  - the runtime never picks target states itself: every transition comes from
    dop.decide(state, facts); waiting states pause WITHOUT transitioning;
  - model-emitted control fields are scrubbed into Facts.ignored_model_fields.

Resumability is a runtime capability (task §7): run snapshots live in the
DocStore port (`"edma_run:{id}"` per-org documents) and are re-written around
every transition.
Snapshots are saved before each component runs, so a crash mid-component
resumes by re-entering that state. ACTION records an at-most-once marker and
relies on verification to catch an absent real-world effect.

Non-progress detection (§23) is semantic first: repeated identical reasoning
outputs / identical verification failures fingerprint to the same decision and
route to ESCALATION_CHECK via the frozen policy. The hard step cap is a
secondary safety net only — never the primary mechanism.

Fully async: cognitive calls await the ModelProvider port (task §5). Tests
inject a fake CognitiveModel via edma_core.runtime.set_model().
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from typing import Optional

from . import dop
from . import ports

# Port OVERRIDES (test seams). None = port registry'dan (lazy in-memory default).
_store: Optional[object] = None   # DocStore
_wallet: Optional[object] = None  # Budget
from .components import CognitiveModel  # noqa: F401  (re-exported for test seams)
from . import actions as EDMA_ACTIONS
from . import ops as EDMA_OPS
from . import verification as EDMA_VERIFY
from . import trace as EDMA_TRACE

RUN_KEY_PREFIX = "edma_run:"
INDEX_KEY = "edma_runs"
MAX_RUNS = 20
MAX_STEPS_HARD = 24          # secondary safety net; primary guard is semantic
MAX_TEXT = 300

# ── COGNITIVE LAYER HARDENING (2026-09-26) ──────────────────────────────
# MINOR-5B: yagona markaziy default. Core'da bu SIYOSAT bayrog'i — deployatsiya
# o'zi set_default_model() bilan brendini belgilaydi (fail-closed qoladi).
EDMA_DEFAULT_MODEL = "default"


def set_default_model(model_id: str) -> str:
    """Deployment-level model policy (one foundation model per deployment)."""
    global EDMA_DEFAULT_MODEL
    m = str(model_id or "").strip().lower()
    if not m:
        raise ModelPolicyError("empty_model_id")
    EDMA_DEFAULT_MODEL = m
    return EDMA_DEFAULT_MODEL
MAX_CONTEXT_MSGS = 12          # FIX-1: bound — xabarlar soni
MAX_CONTEXT_CHARS = 6000       # FIX-1: bound — umumiy belgilar budjeti
MAX_CONTEXT_ITEM_CHARS = 1200  # FIX-1: bir xabar chegarasi
LEASE_TTL_MS = 120_000         # FIX-3: durable lease (worker ownership)
MAX_RESUME_ATTEMPTS = 5        # FIX-3: maksimal resume urinishlari
RUN_STATES = ("running", "pending", "complete", "failed", "escalated", "cancelled")


class ModelPolicyError(Exception):
    """MINOR-5B: model identifikatori siyosatga mos emas — fail closed."""


def resolve_model(run: Optional[dict] = None, explicit: Optional[str] = None) -> str:
    """MINOR-5B: YAGONA markaziy model-resolution funksiyasi.

    Deterministik ustuvorlik: explicit (avtorizatsiyalangan chaqiruv)
    → persisted run modeli → EDMA default. Legacy nomlar normalize qilinadi.
    Noto'g'ri/noma'lum id → ModelPolicyError (FAIL CLOSED — tutorli modelga
    jim o'tish TAQIQLANGAN). Resumed run o'z saqlangan model siyosatini saqlaydi.
    """
    m = str(explicit or "").strip()
    if not m and run:
        m = str(run.get("model") or "").strip()
    if not m:
        m = EDMA_DEFAULT_MODEL
    m = m.lower()
    if m != EDMA_DEFAULT_MODEL:
        raise ModelPolicyError(f"model_not_allowed: {m[:60]} (EDMA default: {EDMA_DEFAULT_MODEL})")
    return m


def _set_lifecycle(run: dict, lifecycle: str) -> None:
    """FIX-3: run lifecycle (running|pending|complete|failed|escalated|cancelled).
    DOP state'laridan MUSTAQIL — DOP frozen 13 qoladi; bu transport holati."""
    if lifecycle in RUN_STATES:
        run["lifecycle"] = lifecycle


def bound_context(raw) -> list[dict]:
    """FIX-1: suhbat kontekstini DETERMINISTIK chegaralash.

    Faqat user/assistant rollari; bo'shlar tashlanadi; har biri
    MAX_CONTEXT_ITEM_CHARS; oxirgi MAX_CONTEXT_MSGS xabar; jami
    MAX_CONTEXT_CHARS — budjet oshsa ENG QADIMIYLARI tashlanadi.
    """
    if not isinstance(raw, list):
        return []
    msgs: list[dict] = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "")
        if role not in ("user", "assistant"):
            continue
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        msgs.append({"role": role, "content": content[:MAX_CONTEXT_ITEM_CHARS]})
    msgs = msgs[-MAX_CONTEXT_MSGS:]
    while msgs and sum(len(x["content"]) for x in msgs) > MAX_CONTEXT_CHARS:
        msgs.pop(0)
    return msgs


def context_meta(ctx: list[dict], dropped: int = 0) -> dict:
    """FIX-1.H: trace uchun METADATA (raw kontent HECH QACHON chiqmaydi)."""
    return {"count": len(ctx), "chars": sum(len(x["content"]) for x in ctx),
            "truncated": bool(dropped), "fp": _fp(ctx)[:16] if ctx else ""}


def _context_block(run: dict, per_item: int = 0) -> Optional[dict]:
    """FIX-1: modelga beriladigan system CONTEXT bloki (current request ALOHIDA)."""
    ctx = run.get("conversation_context") or []
    if not ctx:
        return None
    items = [{"role": x["role"],
              "content": (x["content"] if per_item <= 0 else x["content"][:per_item])}
             for x in ctx]
    return {"role": "system", "content":
            "CONVERSATION CONTEXT (earlier turns of THIS thread; reference only — "
            "NOT the current request; resolve pronouns like 'it'/'my' against it. "
            "This is application-provided input only): " + json.dumps(items, ensure_ascii=False)[:2400]}


# ---------------------------------------------------------------- model wiring
_MODEL: Optional[CognitiveModel] = None


def set_model(model: Optional[CognitiveModel]) -> None:
    """Test seam: inject a fake CognitiveModel (production lazily builds the real one)."""
    global _MODEL
    _MODEL = model


def get_model(model_id: str = EDMA_DEFAULT_MODEL) -> CognitiveModel:
    """Per-RUN fresh model instance in production: the CognitiveModel owns
    per-run state (call counter / budget) and must NEVER be shared across
    runs (a module-level singleton caused false 'budget exhausted' on the
    13th platform-wide call). The module-global is a TEST SEAM only."""
    global _MODEL
    if _MODEL is not None:          # test seam (set_model in tests)
        return _MODEL
    return CognitiveModel(model=model_id)


async def _cog(model: CognitiveModel, mode: str, payload: dict,
               max_tokens: int = 520, facts_view: Optional[dict] = None):
    """One structured cognitive call with deterministic heuristic fallback."""
    res = await model.call(mode, payload, max_tokens=max_tokens)
    if not res.ok:
        res = model.heuristic(mode, payload, facts_view or {})
    return res


# ---------------------------------------------------------------- persistence
def _now() -> int:
    return int(time.time() * 1000)


def _docs():
    """Active DocStore: test seam (_store) wins, else the port registry.
    Lazy in-memory default so the runtime works with ZERO configuration."""
    if _store is not None:
        return _store
    from .defaults import wire_defaults
    wire_defaults()
    return ports.get_docs()


def _save(uid: str, run: dict) -> bool:
    return bool(_docs().user_doc_put(uid, RUN_KEY_PREFIX + run["id"], run))


def _load(uid: str, run_id: str) -> Optional[dict]:
    doc = _docs().user_doc_get(uid, RUN_KEY_PREFIX + run_id)
    return doc if isinstance(doc, dict) and doc.get("id") == run_id else None


def _index_add(uid: str, run: dict) -> None:
    idx = _docs().user_doc_get(uid, INDEX_KEY) or {"runs": []}
    runs = [r for r in idx.get("runs", []) if r.get("id") != run["id"]]
    runs.insert(0, {"id": run["id"], "created": run["created"], "state": run["state"],
                    "status": run.get("status")})
    del runs[MAX_RUNS:]
    _docs().user_doc_put(uid, INDEX_KEY, {"runs": runs})


def _index_update(uid: str, run: dict) -> None:
    idx = _docs().user_doc_get(uid, INDEX_KEY) or {"runs": []}
    for r in idx.get("runs", []):
        if r.get("id") == run["id"]:
            r["state"], r["status"] = run.get("state"), run.get("status")
    _docs().user_doc_put(uid, INDEX_KEY, idx)


def get_run(uid: str, run_id: str) -> Optional[dict]:
    """Isolation: only the owning org's uid can load a run (task §24)."""
    run = _load(uid, run_id)
    if run and run.get("uid") != uid:
        return None
    return run


def delete_run(uid: str, run_id: str) -> bool:
    run = get_run(uid, run_id)
    if not run or run.get("deleted"):
        return False
    ok = _docs().user_doc_put(uid, RUN_KEY_PREFIX + run_id,
                             {"id": run_id, "deleted": True, "uid": uid, "state": dop.END,
                              "status": "deleted", "lifecycle": "cancelled",
                              "trace": (run.get("trace") or [])[-5:]})
    _index_update(uid, {"id": run_id, "state": "DELETED", "status": "deleted"})
    return bool(ok)


def list_runs(uid: str) -> list[dict]:
    idx = _docs().user_doc_get(uid, INDEX_KEY) or {"runs": []}
    return idx.get("runs", [])


def public_view(run: dict) -> dict:
    """API-facing projection of a run."""
    tr = run.get("trace") or []
    return {
        "run_id": run.get("id"), "state": run.get("state"), "status": run.get("status"),
        "lifecycle": run.get("lifecycle"), "model": run.get("model"),
        "response": run.get("response"),
        "question": run.get("question"),
        "approval_request": run.get("approval_request"),
        "steps": len(tr),
        "dop_path": [t.get("state") for t in tr],
        "last_decision": (tr[-1] or {}).get("decision") if tr else None,
        "escalation": run.get("escalation"),
        "verification": run.get("last_verification"),
        "usage": run.get("usage"),
    }


# ---------------------------------------------------------------- model-field scrubbing
def scrub_model_fields(data: dict) -> tuple[dict, dict]:
    """Split model JSON into (semantic_data, ignored_control_fields). Task §10."""
    if not isinstance(data, dict):
        return {}, {"raw": data}
    banned = {"authorized", "approved", "verified", "verification_passed", "state",
              "next_state", "dop", "dop_next", "goto", "transition", "permission",
              "grant", "authorize"}
    ignored = {k: data[k] for k in list(data.keys()) if str(k).lower() in banned}
    semantic = {k: v for k, v in data.items() if k not in ignored}
    return semantic, ignored


# ---------------------------------------------------------------- run lifecycle
def start_run(uid: str, message: str, model: str = EDMA_DEFAULT_MODEL,
              max_model_calls: int = 12, key: Optional[dict] = None,
              conversation_context: Optional[list] = None) -> dict:
    run_id = "edma_" + uuid.uuid4().hex[:12]
    # FIX-1: kontekst INPUT sifatida chegaralanadi; current request (`message`)
    # ALOHIDA saqlanadi. Model kontekstni o'zgartira olmaydi (input-only).
    _ctx_raw = conversation_context or []
    ctx = bound_context(_ctx_raw)
    run = {
        "id": run_id, "uid": uid, "model": model, "message": str(message or "")[:4000],
        "state": dop.START, "status": "created", "created": _now(), "updated": _now(),
        "trace": [], "steps": 0, "model_calls": 0, "max_model_calls": int(max_model_calls),
        "fingerprints": {}, "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        "entered": {},          # write-ahead markers
        # FIX-3: run lifecycle (transport holati; DOP'dan mustaqil)
        "lifecycle": "pending",
        "resume_attempts": 0,   # FIX-3: resume bound
        "lease": None,          # FIX-3: {worker, until} — durable ownership
    }
    if ctx:
        dropped = max(0, len(_ctx_raw) - len(ctx))
        run["conversation_context"] = ctx
        run["conversation_meta"] = context_meta(ctx, dropped)
    _save(uid, run)
    _index_add(uid, run)
    return run


# ---------------------------------------------------------------- helpers
def _fp(parts: list) -> str:
    return hashlib.sha1(json.dumps(parts, default=str, sort_keys=True).encode()).hexdigest()[:16]


def _note_fingerprint(run: dict, kind: str, payload: list) -> bool:
    """Semantic repetition memo. True when the SAME decision repeats (§23)."""
    fp = _fp([kind] + payload)
    seen = int(run["fingerprints"].get(fp, 0)) + 1
    run["fingerprints"][fp] = seen
    if len(run["fingerprints"]) > 64:
        run["fingerprints"] = dict(list(run["fingerprints"].items())[-64:])
    return seen >= 2


def _budget():
    """Active Budget port: test seam (_wallet) wins, else the registry (may be None)."""
    return _wallet if _wallet is not None else ports.get_budget()


def _charge_model_call(uid: str, mode: str, res, run_id: str) -> None:
    """Budget accounting for a cognitive model call (fail-soft by design)."""
    b = _budget()
    if b is None or res.source != "model":
        return
    try:
        u = res.usage or {}
        p, c = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
        if p or c:
            b.charge(uid, b.cost_of({"prompt": p, "completion": c}),
                     "edma_model_call", {"mode": mode, "run_id": run_id})
    except Exception:
        pass


def _balance(uid: str) -> float:
    try:
        b = _budget()
        return float(b.balance(uid)) if b else 0.0
    except Exception:
        return 0.0


def _tier_spec(uid: str) -> dict:
    """Conservative core cap. Deployments replace via plugin caps (ctx tier_spec_fn)."""
    return {"max_samples": 50, "level": 0, "name": "core"}


def _last_user_msg(run: dict) -> str:
    return str(run.get("message") or "")


# MINOR-5C: deterministik til fallback zanjiri (hech qachon bo'sh/invalid emas).
# Bu til TANLOVI uchun fakt — kognitiv javob generatori EMAS (regex-router EMAS).
_EDMA_LANGS = ("en", "uz", "ru", "tr", "es", "de", "fr")


def _valid_lang(x: str) -> str:
    return x if x in _EDMA_LANGS else ""


def _resolve_language(detected: str, run: dict, uid: str) -> str:
    """detected (semantik, model) → kontekst metadata tili → user locale → "uz"."""
    lang = _valid_lang(str(detected or "").strip().lower())
    if lang:
        return lang
    # 1) suhbat konteksti tili (run'ga biriktirilgan metadata fakt)
    lang = _valid_lang(str((run.get("conversation_meta") or {}).get("language") or "").strip().lower())
    if lang:
        return lang
    # 2) user locale (ixtiyoriy profil porti) — deterministik, model emas
    try:
        prof = ports.get_user_profile()
        if prof is not None:
            lang = _valid_lang(str((prof(uid) or {}).get("locale") or "").strip().lower())
            if lang:
                return lang
    except Exception:
        pass
    return "uz"


def view_get(run: dict, k: str, d=None):
    return (run.get("view") or {}).get(k, d)


def view_set(run: dict, k: str, v) -> None:
    run.setdefault("view", {})[k] = v


# ---------------------------------------------------------------- state executors
async def _execute_state(uid: str, run: dict, model: CognitiveModel) -> dop.Facts:
    """Execute the CURRENT state's component; return Facts for the DOP decision."""
    state = run["state"]
    f = dop.Facts()
    msg = _last_user_msg(run)
    view = run.setdefault("view", {})
    # Davlatlararo faktlar (perception qarorlari keyingi holatlarda ham o'qiladi)
    f.effort_hint = str(view.get("effort_hint") or "medium")

    if state == dop.START:
        return f  # no component at START

    if state == dop.PERCEIVING:
        # MEMORY-AGNOSTIC: bu yerda memory capability facti YO'Q — runtime hech
        # qanday memory provider haqida bilmaydi. Application (EDMA Gram)
        # conversation contextni start_run(conversation_context=...) orqali
        # INPUT sifatida beradi (pastda _cblk).
        _pmsgs = [{"role": "user", "content": msg}]
        # FIX-1: suhbat konteksti (bounded) — current request'dan ALOHIDA blok.
        _cblk = _context_block(run)
        if _cblk:
            _pmsgs.append(_cblk)
        res = await _cog(model, "perceive", {"messages": _pmsgs})
        _charge_model_call(uid, "perceive", res, run["id"])
        semantic, ignored = scrub_model_fields(res.data or {})
        f.ignored_model_fields = ignored
        f.clarification_required = bool(semantic.get("requires_clarification"))
        f.intent = str(semantic.get("intent") or "general")[:40]
        _eh = str(semantic.get("effort_hint") or "medium").strip().lower()
        f.effort_hint = _eh if _eh in ("low", "medium", "high") else "medium"
        view["perception"] = semantic
        # §TIL: Perception'ning semantik til fakty (struktural, kod hech narsa
        # aniqlamaydi) — Response kontekstiga uzatiladi (RESPONSE LANGUAGE fact).
        _lang = str(semantic.get("language") or "").strip().lower()
        # MINOR-5C FIX: deterministik til fallback zanjiri — hech qachon bo'sh qolmaydi:
        # detected (semantik, valid) → kontekst/suhbat tili (valid) → user locale → "uz".
        view["user_language"] = _resolve_language(_lang, run, uid)
        view["clarification_question"] = str(semantic.get("clarification_question") or "")[:300]
        view["action_candidate"] = str(semantic.get("action_candidate") or "none")
        view["effort_hint"] = f.effort_hint
        if run.get("clarification_answer"):
            f.clarification_answered = True
        return f

    if state == dop.ENERGY_EVALUATION:
        res = await _cog(model, "energy", {"messages": [{"role": "user", "content": msg}]})
        _charge_model_call(uid, "energy", res, run["id"])
        semantic, ignored = scrub_model_fields(res.data or {})
        f.ignored_model_fields = ignored
        f.complexity = str(semantic.get("complexity") or "low")[:10]
        f.energy_done = True
        # §COMPONENT ROLE: Energy = Reasoning uchun cognitive budget/context.
        # Bu faktlar REASONING facts_view'ga boradi (audit §3: energy facts
        # hisobga olinishi SHART) — lekin Energy hech qachon route tanlamaydi.
        _cl = semantic.get("cognitive_load")
        view["energy"] = {"complexity": f.complexity,
                          "cognitive_load": round(float(_cl), 2) if isinstance(_cl, (int, float)) else None,
                          "expected_operations": str(semantic.get("expected_operations") or "")[:20],
                          "verification_burden": str(semantic.get("verification_burden") or "")[:12],
                          "note": str(semantic.get("note") or "")[:160]}
        return f

    if state == dop.REASONING:
        # §6: CAPABILITIES — deterministik capability state (tool mavjudligi).
        # Bu fakt modelga beriladi; qaror baribir MODELNING semantic reasoning'i.
        _search_ok = _web_search_available()
        capabilities = {
            "web_search": {"available": bool(_search_ok),
                           "cost_per_query": float(getattr(EDMA_ACTIONS._se, "SEARCH_QUERY_COST", 0.002)) if _search_ok else 0.0},
            # CRITICAL-4: ro'yxatdan o'tgan amallar (default-deny registry)
            "actions": sorted(EDMA_ACTIONS.ACTION_REGISTRY.keys()),
        }
        _og = view.get("op_graph") or {}
        facts_view = {
            "perception": view.get("perception") or {},
            "action_candidate": view.get("action_candidate") or "none",
            # §COMPONENT ROLE (audit §3): Energy faktilari — cognitive budget
            # Reasoning'ga YETADI (energy hech qachon route tanlamaydi).
            "energy": view.get("energy") or {},
            "verification_pending": bool(view.get("verification_pending")),
            "verification_target": view.get("verification_target"),
            "clarification_answer": run.get("clarification_answer"),
            "last_action_error": (view.get("last_action") or {}).get("error"),
            "last_verification": view.get("last_verification"),
            "capabilities": capabilities,
            # FIX-2: joriy op-graf holati (safe metadata) + oxirgi op muvaffaqiyatsizligi
            "ops": _og.get("summary"),
            "last_op_failure": view.get("last_op_failure"),
            "op_plan_error": view.get("op_plan_error"),
            # FIX-1: kontekst ishlatilishi metadata (kontent emas)
            "conversation_turns": (run.get("conversation_meta") or {}).get("count", 0),
        }
        payload = {"messages": [
            {"role": "user", "content": msg}]}
        # FIX-1: reasoning ham bounded kontekstni ko'radi (qisqa variant)
        _rcblk = _context_block(run, per_item=200)
        if _rcblk:
            payload["messages"].append(_rcblk)
        payload["messages"].append(
            {"role": "system", "content": "EXECUTION FACTS: " + json.dumps(facts_view, default=str)[:2400]})
        res = await _cog(model, "reason", payload, facts_view=facts_view)
        _charge_model_call(uid, "reason", res, run["id"])
        semantic, ignored = scrub_model_fields(res.data or {})
        f.ignored_model_fields = ignored
        view["reason_source"] = res.source

        # --- semantic non-progress fingerprint (§23) ---
        if _note_fingerprint(run, "reason", [
                semantic.get("propose_action"),
                semantic.get("propose_verification"), semantic.get("escalate"),
                semantic.get("ready"), facts_view.get("last_verification")]):
            f.non_progress = True
            view_set(run, "np_flag", True)          # run-level: ESCALATION_CHECK o'qiydi

        if run.get("steps", 0) >= MAX_STEPS_HARD or model.exhausted:
            f.budget_exhausted = True
            view_set(run, "budget_flag", True)

        # §4/§7: epistemik baho — REASONING factlari (yangi DOP state EMAS).
        # Trace va RESPONSE kontekstiga tushadi.
        ka = semantic.get("knowledge_assessment")
        if isinstance(ka, dict):
            view["knowledge_assessment"] = {
                "status": str(ka.get("status") or "")[:24],
                "confidence": ka.get("confidence") if isinstance(ka.get("confidence"), (int, float)) else None,
                "external_information_needed": bool(ka.get("external_information_needed"))}
        esc = semantic.get("escalate")
        prop_act = semantic.get("propose_action")
        prop_ver = semantic.get("propose_verification")

        # ── FIX-2: ops plan TAKLIFI (semantik) → deterministik validatsiya ──
        # Model DOP state TANLAMAYDI, avtorizatsiya bermaydi — faqat op-graf
        # taklif qiladi; graf qabuli/quyish faqat shu deterministik kodda.
        _raw_ops = semantic.get("ops")
        _fresh_plan = None
        if isinstance(_raw_ops, list) and _raw_ops:
            _ledger = view.get("op_ledger") or {}
            _p, _perr, _pign = EDMA_OPS.validate_revision(_raw_ops, _ledger)
            if _pign:
                run.setdefault("ignored_model_fields", {}).update(_pign)
            if _p is None:
                view["op_plan_error"] = str(_perr)[:80]
                _note_fingerprint(run, "ops_invalid", [str(_perr)])
            else:
                _gver = int((view.get("op_graph") or {}).get("version") or 0)
                if _gver >= EDMA_OPS.MAX_GRAPH_VERSIONS:
                    # §12: re-planning budjeti tugadi → legal ESCALATION_CHECK yo'li
                    f.budget_exhausted = True
                    view_set(run, "budget_flag", True)
                    view["op_plan_error"] = "max_graph_versions"
                else:
                    _statuses = EDMA_OPS.initial_statuses(_p, _ledger)
                    view["op_graph"] = {"version": _gver + 1, "plan": _p,
                                        "statuses": _statuses,
                                        "summary": EDMA_OPS.plan_summary(_p, _statuses)}
                    view["op_plan_error"] = None
                    view["ops_blocked"] = False
                    _fresh_plan = view["op_graph"]

        if isinstance(esc, dict) and esc.get("reason"):
            f.proposal = "escalate"
            f.escalate_reason = str(esc.get("reason"))[:200]
            return f
        if view.get("verification_pending") or (isinstance(prop_ver, dict) and prop_ver.get("target")):
            f.proposal = "verify"
            return f
        if isinstance(prop_act, dict) and prop_act.get("type"):
            f.proposal = "action"
            f.action_proposed = {"type": str(prop_act.get("type"))[:40],
                                 "args": prop_act.get("args") if isinstance(prop_act.get("args"), dict) else {}}
            view["proposed_action"] = f.action_proposed
            return f
        # ── FIX-2: yangi op-graf versiyasi — BITTA tayyor op'ni ACTION'ga ──
        if _fresh_plan is not None:
            _ready = EDMA_OPS.ready_ops(_fresh_plan["plan"], _fresh_plan["statuses"])
            if _ready:
                _op = next(o for o in _fresh_plan["plan"] if o["id"] == _ready[0])
                f.proposal = "action"
                f.action_proposed = {"type": _op["kind"], "args": _op.get("args") or {},
                                     "op_id": _op["id"]}
                view["proposed_action"] = f.action_proposed
                return f
            if EDMA_OPS.pending_left(_fresh_plan["plan"], _fresh_plan["statuses"]):
                # BLOCKED: dep PASS bo'lmagan va bu pass'da revizyon yo'q —
                # halol respond (soxta muvaffaqiyat YO'Q)
                f.proposal = "respond"
                f.ready_to_respond = True
                view["ops_blocked"] = True
                return f
            f.proposal = "respond"
            f.ready_to_respond = True
            return f
        # default: ready to respond (possibly with a draft)
        f.proposal = "respond"
        f.ready_to_respond = True
        draft = str(semantic.get("response_draft") or "")
        if draft:
            view["response_draft"] = draft[:6000]
        return f

    if state == dop.VERIFICATION:
        verdict = None
        if view.get("verification_pending") and view.get("last_action"):
            act = view["last_action"]
            v = EDMA_VERIFY.verify_action(act.get("action"), act.get("result_dict") or {}, uid)
            verdict = v
            # Har qanday YAKUNIY hukm (PASS/FAIL/UNCERTAIN) pending'ni yopadi —
            # eski dalilni qayta-qayta tekshirish non-progress bo'ladi. FAIL'da
            # REASONING yangi dalil qidıradi (re-planning) — audit CRITICAL-2.
            if v.result in (EDMA_VERIFY.PASS, EDMA_VERIFY.FAIL, EDMA_VERIFY.UNCERTAIN):
                view["verification_pending"] = False
        else:
            pass  # MEMORY-AGNOSTIC: memory verification YO'Q (faqat action natijasi)
        if verdict is None:
            verdict = EDMA_VERIFY.Verdict(EDMA_VERIFY.SKIPPED, "action", False,
                                          "no pending work to verify", {})
        view["last_verification"] = verdict.to_dict()
        run["last_verification"] = verdict.to_dict()
        f.verification = verdict.to_dict()
        f.action_executed = not view.get("verification_pending", False) and bool(view.get("last_action"))
        # ── FIX-2: op natijasi LEDGER'ga (immutable tarix) + graf statuslari ──
        _g = view.get("op_graph") or {}
        if _g.get("plan"):
            _op_id2 = str((view.get("last_action") or {}).get("op_id") or "")
            if _op_id2 and verdict.result in (EDMA_VERIFY.PASS, EDMA_VERIFY.FAIL, EDMA_VERIFY.UNCERTAIN):
                _opd = next((o for o in _g["plan"] if o["id"] == _op_id2), None)
                if _opd is not None:
                    _led2 = view.setdefault("op_ledger", {})
                    _prev = _led2.get(_op_id2) or {}
                    # tarixiy IMMUTABILITY: eski PASS yozuvi ustiga yozilmaydi
                    if not (_prev.get("status") == "PASS" and _prev.get("fp") == EDMA_OPS.plan_fingerprint(_opd)):
                        _led2[_op_id2] = {"status": verdict.result,
                                          "fp": EDMA_OPS.plan_fingerprint(_opd),
                                          "version": _g.get("version"), "ts": _now(),
                                          "evidence": str(verdict.reason)[:120]}
                    if _prev.get("status") != "PASS" or _prev.get("fp") != EDMA_OPS.plan_fingerprint(_opd):
                        _g["statuses"][_op_id2] = verdict.result
                    # FIX-2: append-only TARIX (immutable) — FAIL ham yo'qolmaydi
                    _hist = view.setdefault("op_history", [])
                    _hist.append({"op_id": _op_id2, "status": verdict.result,
                                  "fp": EDMA_OPS.plan_fingerprint(_opd),
                                  "version": _g.get("version"), "ts": _now(),
                                  "evidence": str(verdict.reason)[:120]})
                    del _hist[:-32]
                    if verdict.result in (EDMA_VERIFY.FAIL, EDMA_VERIFY.UNCERTAIN):
                        view["last_op_failure"] = {"op_id": _op_id2, "verdict": verdict.result,
                                                   "reason": str(verdict.reason)[:120]}
            f.ops_pending = EDMA_OPS.pending_left(_g["plan"], _g.get("statuses") or {})
        if verdict.result == EDMA_VERIFY.FAIL:
            if _note_fingerprint(run, "verify_fail", [verdict.reason, verdict.evidence_summary]):
                f.non_progress = True
                view_set(run, "np_flag", True)      # run-level: ESCALATION_CHECK o'qiydi
        return f

    if state == dop.ACTION_DECISION:
        prop = view.get("proposed_action") or {}
        spec = EDMA_ACTIONS.ACTION_REGISTRY.get(prop.get("type") or "")
        if spec is None:
            f.action_invalid_reason = "unregistered_action"
            # §COMPONENT ROLE (audit §4): qarorga aylantirish FAQAT deterministik;
            # output fact'lar trace/public view'da ko'rinadi.
            view["action_decision"] = {"registered": False, "args_valid": None,
                                       "reason": "unregistered_action"}
        else:
            ok, clean, err = EDMA_ACTIONS.validate_args(spec, prop.get("args") or {})
            if not ok:
                f.action_invalid_reason = err
                view["action_decision"] = {"registered": True, "args_valid": False,
                                           "reason": str(err)[:80]}
            else:
                # FIX-2: op_id ham o'tadi (op-graf ledger bog'lanishi uchun)
                view["validated_action"] = {"type": spec.name, "args": clean,
                                            "op_id": str(prop.get("op_id") or "")}
                view["action_decision"] = {"registered": True, "args_valid": True,
                                           "reason": "ok"}
        return f

    if state == dop.ACTION_AUTHORIZATION:
        prop = view.get("validated_action") or {}
        ctx = {"uid": uid, "run_id": run["id"], "approval": run.get("approval"),
               "balance_fn": lambda: _balance(uid), "tier_spec_fn": lambda: _tier_spec(uid)}
        az = EDMA_ACTIONS.authorize(prop.get("type"), prop.get("args") or {}, ctx)
        view["authorization"] = az.to_dict()
        f.authorization = az.to_dict()
        if az.status == "pending_human":
            spec = EDMA_ACTIONS.ACTION_REGISTRY[az.action]
            run["approval_request"] = {"action": az.action, "reason": az.reason,
                                       "risk": spec.risk, "args": prop.get("args") or {},
                                       "note": "Answer via /v1/edma/run/{id}/answer {\"approve\": true|false}"}
            run["pending_action"] = {"type": az.action, "args": prop.get("args") or {}}
        return f

    if state == dop.ACTION:
        prop = view.get("validated_action") or {}
        # ── FIX-2/3 IDEMPOTENCY: op allaqachon PASS (ledger) bo'lsa qayta IJRO YO'Q ──
        # (resume crash'dan keyin model xuddi shu op'ni qayta taklif qilsa —
        #  qayta xarajat/qayta yozuv bo'lmaydi; tarixiy natija immutable.)
        _op_id = str(prop.get("op_id") or "")
        if _op_id:
            _led = (view.get("op_ledger") or {}).get(_op_id) or {}
            if _led.get("status") == "PASS":
                f.action_executed = False
                f.action_result = {"ok": True, "action": prop.get("type"),
                                   "skipped": "already_passed", "op_id": _op_id}
                _g = view.get("op_graph") or {}
                f.ops_pending = EDMA_OPS.pending_left(_g.get("plan") or [], _g.get("statuses") or {})
                return f
        # at-most-once write-ahead marker: on crash-resume the ACTION is NOT blindly
        # re-executed; VERIFICATION judges the real world and FAIL routes to REASONING.
        run["entered"][dop.ACTION] = True
        result = EDMA_ACTIONS.execute(prop.get("type"), prop.get("args") or {},
                                      {"uid": uid, "run_id": run["id"]})
        view["last_action"] = {"action": prop.get("type"), "ok": result.ok, "error": result.error,
                               "op_id": _op_id,
                               "result_dict": {"ok": result.ok, "action": result.action,
                                               "output": result.output, "evidence": result.evidence,
                                               "error": result.error, "op_id": _op_id}}
        view["verification_pending"] = True
        view["verification_target"] = "action"
        f.action_executed = True
        f.action_result = result.to_dict()
        return f

    if state == dop.ESCALATION_CHECK:
        # Pure control evaluation: reads RUN-LEVEL posture (non-progress, budget,
        # authorization denial, retries) accumulated from earlier states.
        f.non_progress = bool(view_get(run, "np_flag", False))
        f.budget_exhausted = bool(view_get(run, "budget_flag", False)) or run.get("steps", 0) >= MAX_STEPS_HARD
        authz = view.get("authorization") or {}
        f.authorization = authz
        f.escalate_reason = run.get("escalation_reason")
        f.retries = int(view_get(run, "escalation_retries", 0))
        return f

    if state == dop.ESCALATION:
        # ZERO-TEMPLATE: runtime user-facing NL yozmaydi — faqat struktural
        # faktni qayd etadi. Tushuntirishni RESPONSE'da EDMA Humanoid o'zi yozadi.
        reason = run.get("escalation_reason") or "run could not complete safely"
        run["escalation"] = {"reason": str(reason)[:200], "run_id": run["id"]}
        _set_lifecycle(run, "escalated")
        view["escalated_before"] = True
        return f

    if state == dop.CLARIFICATION_REQUIRED:
        if run.get("clarification_answer"):
            # ZERO-TEMPLATE: javob faqat struktural fact — "qabul qilindi…"
            # kabi runtime NL YO'Q. RESPONSE buni modelga kontekst sifatida
            # beradi va EDMA Humanoid tabiiy javobni yozadi.
            ans = str(run.get("clarification_answer"))[:600]
            f.clarification_answered = True
            view["clarification_answer"] = ans
        else:
            # Savol matni FAQAT model perceive'dan keladi (heuristic bo'sh
            # qaytaradi) — runtime canned savol YO'Q.
            run["question"] = str(view.get("clarification_question") or "")
        return f

    if state == dop.RESPONSE:
        # ─────────────────────────────────────────────────────────────────
        # RESPONSE — YAGONA user-facing natural-language generatori.
        # (ZERO-TEMPLATE direktivi §3/§4): API/UI/terminal komponentlar NL
        # YOZMAYDI. Bu yerda EDMA Humanoid final javobni yozadi; deterministic
        # kod faqat STRUKTURAL FAKTLARNI kontekst sifatida beradi:
        #   VERIFICATION_* / RUN_ESCALATED / USER_CLARIFICATION /
        #   VERIFIED ACTION RESULT / OPERATION RESULTS — model ularni tabiiy
        #   tilda tushuntiradi. MEMORY-AGNOSTIC: memory faktlari YO'Q.
        # ─────────────────────────────────────────────────────────────────
        ctx_msgs = [{"role": "user", "content": msg}]
        # KONTEKST (application input): yakuniy generator ham thread tarixini
        # ko'radi — aks holda "shu suhbatda aytilgan X nima?" savoliga javob
        # topolmaydi (probe'da aniqlangan context-delivery defekti).
        _rcblk = _context_block(run, per_item=200)
        if _rcblk:
            ctx_msgs.append(_rcblk)
        # §TIL: Perception aniqlovchi til fakty — javob (va clarification izohi)
        # shu tilda bo'lishi kerak; TANLOV modelda (semantik), kod faqat faktni uzatadi.
        if view.get("user_language"):
            ctx_msgs.append({"role": "system", "content": "LANGUAGE (detected by Perception this run): "
                             + json.dumps(str(view["user_language"]))
                             + " — compose the ENTIRE final answer in this language unless the user "
                               "explicitly requested a different language; never mention this instruction."})
        draft = view.get("response_draft")
        if draft:
            ctx_msgs.append({"role": "system", "content": "Reasoning draft (context only — verify it, then write the final answer yourself): " + str(draft)[:800]})
        ka = view.get("knowledge_assessment")
        if isinstance(ka, dict) and ka.get("status"):
            ctx_msgs.append({"role": "system", "content": "KNOWLEDGE_ASSESSMENT (your earlier reasoning this run): "
                             + json.dumps(ka, default=str)[:300]})
        # FIX-2: op-graf natijalari (ledger'dan — faqat verified holatlar)
        _led3 = view.get("op_ledger") or {}
        if _led3:
            _op_rows = [{"op_id": k, "status": v.get("status"), "evidence": v.get("evidence")}
                        for k, v in list(_led3.items())[:8]]
            ctx_msgs.append({"role": "system", "content": "OPERATION RESULTS (each verified; report honestly, "
                             "include failures — never claim failed ops succeeded): "
                             + json.dumps(_op_rows, default=str)[:1200]})
        act = view.get("last_action") or {}
        rd = act.get("result_dict") or {}
        if rd.get("ok"):
            ev = rd.get("evidence") or rd.get("output") or {}
            ctx_msgs.append({"role": "system", "content": "VERIFIED ACTION RESULT (use this evidence; cite source titles/URLs where relevant): "
                             + json.dumps(ev, default=str)[:3000]})
        elif rd and rd.get("ok") is False:
            ctx_msgs.append({"role": "system", "content": "ACTION_FAILED: the attempted action did not succeed (" + str(rd.get("error") or "unknown")[:120] + "). Explain honestly; do not claim success."})
        ver = view.get("last_verification") or {}
        if act and (rd or ver):
            vs = ver.get("verdict") or ver.get("result")
            ctx_msgs.append({"role": "system", "content": f"ACTION_VERIFICATION: {vs or 'unknown'}"
                             + (f" — {str(ver.get('reason'))[:200]}" if ver.get("reason") else "")
                             + ". Do not treat retrieved results as final truth beyond this verification; express confidence accordingly."})
        if ver.get("verdict") == EDMA_VERIFY.FAIL:
            ctx_msgs.append({"role": "system",
                             "content": "VERIFICATION_FAILED: last action verification FAILED — do not claim success."})
        elif ver.get("verdict") == EDMA_VERIFY.UNCERTAIN:
            ctx_msgs.append({"role": "system",
                             "content": "VERIFICATION_UNCERTAIN: evidence is inconclusive — express appropriate uncertainty; never fake certainty."})
        esc = run.get("escalation")
        if esc:
            ctx_msgs.append({"role": "system", "content": "RUN_ESCALATED: the run was safely stopped ("
                             + str(esc.get("reason") or "policy")[:160] + "). Explain the outcome honestly and what the user can do next; do not claim success."})
        if view.get("clarification_answer"):
            ctx_msgs.append({"role": "system", "content": "USER_CLARIFICATION: the user answered your clarifying question: "
                             + str(view["clarification_answer"])[:600]
                             + " — incorporate it and deliver the final answer for the original request."})

        text, r_err = await _respond_generate(uid, run, model, ctx_msgs)
        f.ignored_model_fields = getattr(f, "ignored_model_fields", None)
        f.ready_to_respond = True
        if text:
            f.response_text = str(text)[:6000]
        else:
            # ZERO-TEMPLATE: model NL yozolmadi → hech qanday soxta "javob"
            # YARATILMAYDI. Faqat STRUKTURAL muvaffaqiyatsizlik faktni yozamiz —
            # transport qatlami (main.py) buni structured 503 sifatida qaytaradi.
            run["response_failed"] = {"code": "PROVIDER_FAILED", "detail": str(r_err)[:200]}
            _set_lifecycle(run, "failed")
            f.error = str(r_err)[:200]
        run["response"] = f.response_text or ""

    return f  # END / unknown: no component work


# ---------------------------------------------------------------- the loop
async def advance_run(uid: str, run: dict, max_steps: Optional[int] = None) -> dict:
    """Advance the DOP loop until END, a human wait, or budget. Returns public view."""
    # MINOR-5B: markaziy model-resolution — FAIL CLOSED (jim fallback YO'Q)
    try:
        model = get_model(resolve_model(run))
    except ModelPolicyError as e:
        run["status"] = "failed_model_policy"
        _set_lifecycle(run, "failed")
        run["escalation_reason"] = str(e)[:160]
        _save(uid, run)
        return public_view(run)
    _set_lifecycle(run, "running")
    limit = int(max_steps or MAX_STEPS_HARD)
    for _step in range(limit):
        state = run["state"]
        if state == dop.END:
            run["status"] = "complete"
            _set_lifecycle(run, "escalated" if run.get("escalation") else "complete")
            _save(uid, run)
            return public_view(run)

        f = await _guarded_execute(uid, run, model, state)
        run["model_calls"] = model.calls

        # -- human waits pause WITHOUT a transition (frozen graph respected) --
        if state == dop.CLARIFICATION_REQUIRED and not f.clarification_answered:
            run["status"] = "waiting_clarification"
            _set_lifecycle(run, "pending")
            _finish_step(uid, run, state, f, decision=None, waiting=True)
            run["updated"] = _now()
            _save(uid, run)
            return public_view(run)
        if state == dop.ACTION_AUTHORIZATION and f.authorization.get("status") == "pending_human":
            run["status"] = "waiting_approval"
            _set_lifecycle(run, "pending")
            _finish_step(uid, run, state, f, decision=None, waiting=True)
            run["updated"] = _now()
            _save(uid, run)
            return public_view(run)

        # retries bookkeeping for ESCALATION_CHECK -> REASONING edges (§15/§23)
        if state == dop.ESCALATION_CHECK:
            f.retries = int(view_get(run, "escalation_retries", 0))
            if dop.decide(state, f).nxt == dop.REASONING:
                view_set(run, "escalation_retries", int(view_get(run, "escalation_retries", 0)) + 1)

        decision = dop.decide(state, f)

        if state == dop.REASONING and decision.nxt == dop.ESCALATION_CHECK:
            run["escalation_reason"] = f.escalate_reason or f.error or (
                "non-progress" if f.non_progress else "budget exhausted" if f.budget_exhausted
                else "reasoning produced no actionable proposal")
        if state == dop.ACTION_AUTHORIZATION and decision.nxt == dop.ESCALATION_CHECK:
            run["escalation_reason"] = f"authorization: {f.authorization.get('reason')}"

        _finish_step(uid, run, state, f, decision)
        run["state"] = decision.nxt
        run["updated"] = _now()
        _save(uid, run)

        if decision.nxt == dop.END:
            run["status"] = "complete"
            _set_lifecycle(run, "escalated" if run.get("escalation") else "complete")
            _save(uid, run)
            return public_view(run)

    # FIX-3: qasddan qisqa slice (background/porsiyali bajarilish) → PENDING
    # (worker davom etadi); to'liq budjet tugasa → failed (terminal).
    if int(max_steps or 0) and int(max_steps) < MAX_STEPS_HARD:
        run["status"] = "slice_pending"
        _set_lifecycle(run, "pending")
    else:
        run["status"] = "budget_steps"
        _set_lifecycle(run, "failed")
    run["escalation_reason"] = run.get("escalation_reason") or "step limit reached"
    _save(uid, run)
    return public_view(run)


_RESPONSE_TEXT_KEYS = ("text", "answer", "response", "message", "content", "result")


def _extract_response_text(data: dict) -> str:
    """Model JSON'ining qaysi kalitida matn borligini moslashtirish (key-drift).

    Response contract `{"text": ...}` — lekin real modellar ba'zan
    {"answer": ...} / {"response": ...} qaytaradi. Bu KEY-DRIFT — kognitiv
    emas, format moslashuvi: matn baribir RESPONSDA yozilgan model matni."""
    if not isinstance(data, dict):
        return ""
    for k in _RESPONSE_TEXT_KEYS:
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            # Nemotron ba'zan ichma-ich qaytaradi: {"text": {"text": "..."}}.
            for k2 in _RESPONSE_TEXT_KEYS:
                v2 = v.get(k2)
                if isinstance(v2, str) and v2.strip():
                    return v2.strip()
    return ""


async def _respond_generate(uid: str, run: dict, model: CognitiveModel,
                            ctx_msgs: list) -> tuple:
    """RESPONSE matnini FAQAT real modeldan olish (yagona NL generatori).

    • 2 urinish + qisqa backoff (transient 429/timeout uchun).
    • Key-drift qabul qilinadi ({"answer": ...} ham model javobi).
    • Hech qanday heuristic/canned NL FALLBACK YO'Q — omadsizlikda
      (None, structured_reason) qaytadi; user-facing matn IXTIRO QILINMAYDI."""
    last_err = None
    for attempt in range(2):
        res = await model.call("respond", {"messages": ctx_msgs}, max_tokens=2000)
        _charge_model_call(uid, "respond", res, run["id"])
        if res.ok and isinstance(res.data, dict):
            semantic, ignored = scrub_model_fields(res.data)
            if ignored:
                run.setdefault("ignored_model_fields", {}).update(ignored)
            text = _extract_response_text(semantic)
            if text:
                return text, None
            last_err = f"model_json_without_text:{sorted(semantic.keys())[:5]}"
        else:
            last_err = getattr(res, "error", None) or "respond_call_failed"
        if attempt == 0:
            try:
                await asyncio.sleep(0.8)  # transient rate-limit/timeout backoff
            except Exception:
                pass
    return None, last_err


# ---------------------------------------------------------------- FIX-3: resume/worker
def claim_run(uid: str, run_id: str, worker_id: str,
              ttl_ms: int = LEASE_TTL_MS) -> tuple[Optional[dict], str]:
    """Durable ownership (store orqali — in-process lock EMAS).

    Pending run'ni xavfsiz olish: lease faol bo'lsa (boshqa worker) → rad;
    muddati o'tgan lease → qayta olish mumkin (deterministik recovery).
    MAX_RESUME_ATTEMPTS oshsa → failed (cheksiz retry YO'Q).
    """
    run = get_run(uid, run_id)
    if not run or run.get("deleted"):
        return None, "not_found"
    if run.get("lifecycle") not in ("pending", "running"):
        return None, f"not_resumable:{run.get('lifecycle')}"
    lease = run.get("lease") or {}
    now = _now()
    if lease.get("until") and int(lease["until"]) > now and lease.get("worker") != worker_id:
        return None, "leased"
    attempts = int(run.get("resume_attempts") or 0) + 1
    if attempts > MAX_RESUME_ATTEMPTS:
        _set_lifecycle(run, "failed")
        run["status"] = "resume_exhausted"
        run["lease"] = None
        _save(uid, run)
        return None, "max_attempts"
    run["lease"] = {"worker": str(worker_id)[:60], "until": now + int(ttl_ms)}
    run["resume_attempts"] = attempts
    _save(uid, run)
    return run, "claimed"


def release_run(uid: str, run: dict) -> None:
    run["lease"] = None
    _save(uid, run)


async def resume_run(uid: str, run_id: str, worker_id: str = "inline",
                     max_steps: Optional[int] = None) -> dict:
    """Snapshot'dan Xuddi SHU runtime bilan davom etish (ikkinchi engine YO'Q).

    Idempotent: op PASS ledger orqali; lease orqali parallel worker himoyasi.
    """
    run, why = claim_run(uid, run_id, worker_id)
    if run is None:
        return {"error": {"message": f"cannot resume run: {why}",
                          "type": "invalid_request_error", "code": "resume_denied",
                          "reason": why}}
    try:
        view = await advance_run(uid, run, max_steps=max_steps)
    finally:
        fresh = get_run(uid, run_id) or run
        release_run(uid, fresh)
    return view


def discover_pending(user_ids: list[str], limit: int = 10) -> list[dict]:
    """Pending run'larni topish (per-user index'dan; bounded)."""
    now = _now()
    out: list[dict] = []
    for uid in user_ids[:400]:
        try:
            idx = _docs().user_doc_get(uid, INDEX_KEY) or {}
        except Exception:
            continue
        for r in (idx.get("runs") or []):
            run = _load(uid, r.get("id") or "")
            if not run or run.get("deleted"):
                continue
            if run.get("lifecycle") not in ("pending", "running"):
                continue
            lease = run.get("lease") or {}
            if lease.get("until") and int(lease["until"]) > now:
                continue
            out.append({"uid": uid, "run_id": run["id"], "state": run.get("state")})
            if len(out) >= limit:
                return out
    return out


def _web_search_available() -> bool:
    """§6: capability state — deterministik (search layer + wallet mavjudligi).
    Bu QIDIRUV EMAS, faqat imkoniyat faktni — Reasoning modelga qaror uchun."""
    se = getattr(EDMA_ACTIONS, "_se", None)
    wal = getattr(EDMA_ACTIONS, "_wallet", None)
    return bool(se is not None and wal is not None)


async def _guarded_execute(uid: str, run: dict, model: CognitiveModel, state: str) -> dop.Facts:
    """Execute a component; convert any exception into explicit error Facts."""
    try:
        return await _execute_state(uid, run, model)
    except Exception as e:
        return dop.Facts(error=f"component_exception[{state}]: {str(e)[:160]}")


def _finish_step(uid: str, run: dict, state: str, f: dop.Facts,
                 decision: Optional[dop.Decision], waiting: bool = False) -> None:
    run["steps"] = run.get("steps", 0) + 1
    run["model_calls"] = model_calls_of(run)
    component = {"model_calls": run.get("model_calls", 0)}
    if f.verification:
        component["verification"] = f.verification
    if f.authorization:
        component["authorization"] = f.authorization
    if f.action_proposed:
        component["action_proposed"] = f.action_proposed
    if f.action_invalid_reason:
        component["action_invalid_reason"] = f.action_invalid_reason
    if f.ignored_model_fields:
        component["ignored_model_fields"] = f.ignored_model_fields
    if f.error:
        component["error"] = f.error
    if f.non_progress:
        component["non_progress"] = True
    # §4: epistemik baho — REASONING'ning REAL kognitiv fakti (view'dan),
    # trace = actual execution tamoyili bo'yicha event'ga yoziladi.
    _view = run.get("view") or {}
    _ka = _view.get("knowledge_assessment")
    if isinstance(_ka, dict) and _ka.get("status"):
        component["epistemic"] = {"knowledge_assessment": _ka}
    if _view.get("user_language"):
        component["language"] = str(_view["user_language"])[:8]  # trace = actual execution
    # FIX-1: kontekst ishlatilishi METADATA (raw kontent hech qachon trace'ga kirmaydi)
    if state == dop.PERCEIVING and run.get("conversation_meta"):
        component["conversation_context"] = run["conversation_meta"]
    # FIX-2: op-graf versiyasi/metadatasi (args kontenti emas)
    _og2 = _view.get("op_graph")
    if state == dop.REASONING and _og2:
        component["op_graph"] = {"version": _og2.get("version"), "summary": _og2.get("summary")}
    EDMA_TRACE.emit(run, state,
                    decision.to_dict() if decision else {"next": state, "reason": "waiting for human"},
                    component, {"waiting": True} if waiting else _facts_summary(f))
    _save(uid, run)



def _facts_summary(f: dop.Facts) -> dict:
    return {"proposal": f.proposal, "ready": f.ready_to_respond,
            "clarification_required": f.clarification_required,
            "complexity": f.complexity,
            "non_progress": f.non_progress, "budget_exhausted": f.budget_exhausted,
            "error": f.error}


def model_calls_of(run: dict) -> int:
    return int(run.get("model_calls", 0))


# ---------------------------------------------------------------- public ops
async def start_and_advance(uid: str, message: str, model: str = EDMA_DEFAULT_MODEL,
                            key: Optional[dict] = None,
                            conversation_context: Optional[list] = None) -> dict:
    if _wallet is not None and _balance(uid) <= 0:
        return {"error": {"message": "Insufficient funds for an EDMA run. Add credits at /api/billing/credits.",
                          "type": "insufficient_quota", "code": "no_balance"}}
    run = start_run(uid, message, model=model, key=key,
                    conversation_context=conversation_context)
    return await advance_run(uid, run)


async def answer_run(uid: str, run_id: str, text: str = "", approve: Optional[bool] = None) -> dict:
    """Feed a clarification answer OR an approval decision, then continue the loop."""
    run = get_run(uid, run_id)
    if not run or run.get("deleted"):
        return {"error": {"message": "Run not found.", "type": "invalid_request_error",
                          "code": "run_not_found"}}
    if approve is not None:
        if run["state"] != dop.ACTION_AUTHORIZATION:
            return {"error": {"message": f"Run is not awaiting approval (state={run['state']}).",
                              "type": "invalid_request_error", "code": "not_awaiting_approval"}}
        run["approval"] = "granted" if approve else "denied"
        run["approval_request"] = None
        run["status"] = "running"
        _set_lifecycle(run, "running")
    elif text:
        if run["state"] != dop.CLARIFICATION_REQUIRED:
            return {"error": {"message": f"Run is not awaiting clarification (state={run['state']}).",
                              "type": "invalid_request_error", "code": "not_awaiting_clarification"}}
        run["clarification_answer"] = str(text)[:600]
        run["status"] = "running"
        _set_lifecycle(run, "running")
    else:
        return {"error": {"message": "Provide 'text' or 'approve'.",
                          "type": "invalid_request_error", "code": "empty_answer"}}
    _save(uid, run)
    return await advance_run(uid, run)
