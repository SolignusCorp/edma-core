"""§3/§1 EDMA execution binding — bench ↔ EDMA-CORE ko'prigi (FAQAT bu qatlam
edma_core'ni import qiladi). Evaluator/auditor/metrics buni KO'RMAYDI — ular
faqat artifact dict oladi. Bu qatlam runtime holatini O'ZGARTIRMAYDI — faqat
izolyatsiya qilingan shimlar bilan bajaradi (har run o'z havzasida).

Core'dagi izolyatsiya portlar orqali: RT._store/_wallet test-seamlari +
ports registry + ACTION_REGISTRY saqlash/tiklash (har run oxirida hammasi
qayta tiklanadi — prod store'ga TEKINMAYDI).
"""
from __future__ import annotations

import copy
import json
import time

from edma_core import actions as _EA
from edma_core import contrib as _EC

# ── edma_core import: FAQAT adapter qatlamida ──
from edma_core import runtime as _RT
from edma_core.components import CognitiveModel, ComponentResult


class _MemShim:
    """Izolyatsiya: benchmark o'z in-memory doc havzasida ishlaydi."""

    def __init__(self):
        self.docs = {}

    def user_doc_get(self, uid, key):
        return self.docs.get((uid, key))

    def user_doc_put(self, uid, key, data):
        self.docs[(uid, key)] = copy.deepcopy(data)
        return True


class _BenchWallet:
    def __init__(self, budget: float = 1_000.0):
        self.spent = 0.0
        self.budget = budget

    def cost_of(self, spec):
        return 0.002

    def balance(self, uid):
        return self.budget - self.spent

    def try_spend(self, uid, amount, reason=None, meta=None):
        if self.balance(uid) < float(amount):
            raise RuntimeError("insufficient funds")
        self.spent += float(amount)
        return True

    def charge(self, uid, amount, reason=None, meta=None):
        self.try_spend(uid, amount, reason, meta)
        return {}


class _ScriptedSearch:
    """web.search dunyosi — case scenario'dan (control bilan BIR XIL script)."""

    def __init__(self, results_script: list):
        self.script = list(results_script or [])
        self.calls = 0

    def search(self, q, n):
        self.calls += 1
        item = self.script.pop(0) if self.script else {"results": [], "provider": "bench"}
        res = dict(item)
        res.setdefault("provider", "bench")
        res.setdefault("results", [])
        return res


class ScriptedEdmaModel(CognitiveModel):
    """Golden-phase deterministik EDMA "model" — mode scriptlari bilan.
    Keyingi bosqichda bu o'rniga BenchmarkModelAdapter'ni provider sifatida
    ishlatgan haqiqiy CognitiveModel keladi (interfeys O'ZGARMAYDI)."""

    def __init__(self, scripts: dict):
        super().__init__(model="bench-scripted")
        self.scripts = {k: (list(v) if isinstance(v, list) else v)
                        for k, v in (scripts or {}).items()}
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}

    async def call(self, mode, payload, max_tokens=520, temperature=0.2):
        self.calls += 1  # runtime o'z hisobini wrapper'dan olishi uchun
        item = self.scripts.get(mode)
        data = None
        if isinstance(item, list):
            data = item.pop(0) if item else None
        elif isinstance(item, dict):
            data = item
        if data is None:
            return ComponentResult(mode=mode, ok=False, error="bench_script_exhausted")
        self.usage["prompt_tokens"] += 12
        self.usage["completion_tokens"] += 12
        return ComponentResult(mode=mode, ok=True, data=dict(data), source="model",
                               provider="bench-scripted", simulated=False,
                               usage={"prompt_tokens": 12, "completion_tokens": 12})


def extract_json(text: str):
    """Model matnidan birinchi BALANSlangan JSON obyektni oladi (fence/chatter
    tolerant). Topilmasa None — hech qachon soxta dict qaytarmaydi."""
    if not text:
        return None
    t = str(text).strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
    start = t.find("{")
    if start < 0:
        return None
    dec = json.JSONDecoder()
    try:
        obj, _end = dec.raw_decode(t[start:])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        end = t.rfind("}")
        if end <= start:
            return None
        try:
            obj, _end = dec.raw_decode(t[start:end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None


class AdapterCognitiveModel(CognitiveModel):
    """BenchmarkModelAdapter (masalan OpenAICompatAdapter) → EDMA CognitiveModel.
    Har komponent mode'i uchun QAT'IY JSON ko'rsatma; javob deterministik
    parse qilinadi — parse bo'lmasa ok=False (runtime halol component error
    yo'li bilan ketadi; hech qachon soxta semantic maydon IXTIRO QILINMAYDI)."""

    MODE_PROMPTS = {
        "perceive": ('Analyze the user message for an orchestration system. Reply with ONLY a JSON '
                     'object, no prose: {"intent": "<1-2 words>", "requires_clarification": true|false, '
                     '"clarification_question": "<question in the user\'s language or empty>", '
                     '"effort_hint": "low"|"medium"|"high", "action_candidate": "none"|"web.search"|"dataset.create", '
                     '"language": "<ISO code of user language>"} '
                     'requires_clarification=true ONLY if the request is genuinely ambiguous.'),
        "energy": ('Assess the cognitive energy/budget for this request. Reply with ONLY JSON: '
                   '{"complexity": "low"|"medium"|"high", "cognitive_load": <0.0-1.0>, '
                   '"expected_operations": "none"|"one"|"several", "verification_burden": "low"|"medium"|"high", '
                   '"note": "<max 1 sentence>"}'),
        "reason": ('You are the REASONING component of an orchestration runtime. You receive EXECUTION FACTS '
                   'as JSON. Decide the next step and reply with ONLY ONE JSON object, no prose. Choose exactly one: '
                   '1) final answer ready: {"ready": true, "response_draft": "<answer in user language>"} '
                   '2) need a tool: {"ops": [{"id": "op1", "kind": "web.search", "args": {"query": "...", "max_results": 5}, "depends_on": []}]} '
                   '3) cannot proceed honestly: {"escalate": {"reason": "<short reason>"}} '
                   'Rules: use ONLY tools kind "web.search"; NEVER invent facts; if the EXECUTION FACTS show a '
                   'failed/empty tool result, do NOT pretend success - re-plan with a better query, or respond '
                   'honestly about uncertainty, or escalate.'),
        "respond": ('Write the final user-facing answer based on the provided facts and draft. Reply with ONLY '
                    'JSON: {"text": "<answer in the user\'s language>"} Be honest: do not claim verified facts '
                    'that the facts do not support.'),
    }
    GENERIC_PROMPT = ('You are an orchestration component. Reply with ONLY a JSON object relevant to the mode '
                      'given in the messages. No prose outside JSON.')
    JSON_REMINDER = ('Your previous reply was not a JSON object. Reply again with ONLY the JSON '
                     'object exactly as instructed - no prose, no markdown fences.')

    def __init__(self, adapter, max_tokens: int = 520):
        super().__init__(model=getattr(adapter, "model_id", "adapter-model"))
        self.adapter = adapter
        self.default_max_tokens = int(max_tokens)
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def _generate(self, msgs, temperature, max_tokens):
        """Bir model chaqiruv: usage/calls bu yerda yuritiladi; xato — None."""
        self.calls += 1
        try:
            res = self.adapter.generate(msgs, temperature=temperature, max_tokens=max_tokens)
        except Exception as e:  # tarmoq/quota — halol component error
            return None, f"adapter_error: {type(e).__name__}"
        self.usage["prompt_tokens"] += int((res.usage or {}).get("input_tokens") or 0)
        self.usage["completion_tokens"] += int((res.usage or {}).get("output_tokens") or 0)
        return res, None

    async def call(self, mode, payload, max_tokens=None, temperature=0.2):
        if max_tokens is None:
            max_tokens = self.default_max_tokens
        sys_prompt = self.MODE_PROMPTS.get(mode, self.GENERIC_PROMPT)
        msgs = [{"role": "system", "content": sys_prompt}]
        for m in (payload or {}).get("messages") or []:
            msgs.append({"role": m.get("role"), "content": str(m.get("content") or "")})

        res, err = self._generate(msgs, temperature, max_tokens)
        if err:
            return ComponentResult(mode=mode, ok=False, error=err, source="model", usage={})
        raw = str(res.text or "")

        # respond: skalyar/matn javob = butun matn javobdir (DETERMINISTIK normallashtirish,
        # semantic IXTIRO EMAS — respond shartnomasi {"text": <javob>}).
        if mode == "respond" and raw.strip() and extract_json(raw) is None:
            return ComponentResult(mode=mode, ok=True, data={"text": raw.strip()}, source="model",
                                   provider=getattr(self.adapter, "name", "adapter"),
                                   usage={"prompt_tokens": 0, "completion_tokens": 0})

        data = extract_json(raw)
        if data is None and raw.strip():
            # Halol PROTOCOL retry: JSON shartnomasini eslatib BIR marta qayta so'rash
            # (semantikani o'zimiz TO'LDIRMAYMIZ — modelning o'z javobidan olamiz).
            res2, err2 = self._generate(msgs + [{"role": "user", "content": self.JSON_REMINDER}],
                                        temperature, max_tokens)
            if not err2:
                raw2 = str(res2.text or "")
                if mode == "respond" and raw2.strip() and extract_json(raw2) is None:
                    return ComponentResult(mode=mode, ok=True, data={"text": raw2.strip()},
                                           source="model",
                                           provider=getattr(self.adapter, "name", "adapter"),
                                           usage={"prompt_tokens": 0, "completion_tokens": 0})
                data = extract_json(raw2)

        if data is None:
            return ComponentResult(mode=mode, ok=False, error="model_json_unparseable",
                                   source="model",
                                   usage={"prompt_tokens": 0, "completion_tokens": 0})
        return ComponentResult(mode=mode, ok=True, data=data, source="model",
                               provider=getattr(self.adapter, "name", "adapter"),
                               usage={"prompt_tokens": 0, "completion_tokens": 0})


class EdmaHarness:
    """Bir case'ni izolyatsiya qilingan EDMA-CORE runtime'da bajaradi va ARTIFACT
    qaytaradi. Har run o'z havzasida (ports + RT seams saqlanadi/tiklanadi);
    web.search — scripted plugin (registry ham tiklanadi)."""

    def __init__(self, edma_version: str = "13-state-evidence-driven"):
        self.edma_version = edma_version
        self._saved = None

    def _install(self, search_script: list):
        import edma_core.verification as _V
        self._saved = {
            "rt_store": _RT._store, "rt_wallet": _RT._wallet, "rt_model": _RT._MODEL,
            "ver_store": _V._store,
            "registry": dict(_EA.ACTION_REGISTRY),
        }
        self.shim = _MemShim()
        self.wallet = _BenchWallet()
        self.search = _ScriptedSearch(search_script)
        _RT._store = self.shim
        _RT._wallet = self.wallet
        _RT._MODEL = None
        _V._store = None
        _EA.ACTION_REGISTRY.clear()
        _EA.ACTION_REGISTRY.update({
            "web.fetch": _EA.ACTION_REGISTRY  # placeholder (restore below)
        }) if False else None
        # scripted web.search plugin (deterministik dunyo):
        spec = _EC.make_web_search_spec(
            self.search.search,
            spend_fn=lambda uid, cost, reason, meta: self.wallet.try_spend(uid, cost, reason, meta))
        _EA.register(spec)

    def _restore(self):
        import edma_core.verification as _V
        s = self._saved or {}
        _RT._store = s.get("rt_store")
        _RT._wallet = s.get("rt_wallet")
        _RT._MODEL = s.get("rt_model")
        _V._store = s.get("ver_store")
        _EA.ACTION_REGISTRY.clear()
        _EA.ACTION_REGISTRY.update(s.get("registry") or {})

    def run(self, case: dict, model_scripts: dict | None = None,
            search_script: list | None = None, temperature: float = 0.2,
            cognitive_model: CognitiveModel | None = None) -> dict:
        import asyncio
        self._install(search_script or [])
        t0 = time.perf_counter()
        try:
            model = cognitive_model or ScriptedEdmaModel(model_scripts or {})
            _RT.set_model(model)
            uid = "bench_" + str(case.get("case_id") or "case").replace("-", "_")
            ctx = [{"role": m.get("role"), "content": str(m.get("content") or "")}
                   for m in (case.get("conversation_context") or [])]

            async def _go():
                v = await _RT.start_and_advance(uid, case["input"],
                                                model=_RT.EDMA_DEFAULT_MODEL,
                                                conversation_context=ctx)
                r = _RT.get_run(uid, v.get("run_id") or "") or {}
                return v, r

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                view, run = asyncio.run(_go())
            else:  # benchmark sync path'da bo'lmasligi kerak — xavfsiz ko'prik
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(1) as ex:
                    view, run = ex.submit(asyncio.run, _go()).result()
            return self._artifact(case, view, run, model, time.perf_counter() - t0)
        finally:
            self._restore()

    def _artifact(self, case, view, run, model, secs) -> dict:
        v = run.get("view") or {}
        usage = run.get("usage") or {}
        # ── observable action records (ICHKI view — public emas) ──
        records = []
        la = v.get("last_action") or {}
        if la:
            rd = la.get("result_dict") or {}
            records.append({"type": la.get("action"), "args": (v.get("validated_action") or {}).get("args"),
                            "executed": True, "ok": bool(la.get("ok")),
                            "evidence_present": bool(rd.get("evidence")),
                            "approved": str((v.get("authorization") or {}).get("status")) == "granted",
                            "requires_approval": False, "op_id": la.get("op_id")})
        approval_requested = bool(run.get("approval_request"))
        ver = v.get("last_verification") or None
        ver_out = None
        if ver:
            ver_out = {"verdict": ver.get("verdict") or ver.get("result"),
                       "evidence_present": bool(ver.get("evidence_summary"))}
        events = []
        for e in run.get("trace") or []:
            events.append({"seq": e.get("seq"), "state": e.get("state"),
                           "decision": {"next": (e.get("decision") or {}).get("next"),
                                        "reason": (e.get("decision") or {}).get("reason")},
                           "component": {"ignored_model_fields": (e.get("component") or {}).get("ignored_model_fields") or {}}})
        errors = int(bool(v.get("error"))) + int(bool(run.get("response_failed")))
        op_order = [{"seq": i + 1, "op_id": h.get("op_id"), "status": h.get("status")}
                    for i, h in enumerate(v.get("op_history") or [])]
        return {
            "mode": "edma",
            "final_text": str(view.get("response") or ""),
            "model_calls": int(getattr(model, "calls", 0) or 0),
            "tokens": {"input": int((getattr(model, "usage", None) or usage).get("prompt_tokens") or 0),
                       "output": int((getattr(model, "usage", None) or usage).get("completion_tokens") or 0)},
            "latency_ms": round(secs * 1000, 1),
            "error_count": errors,
            "action_records": records,
            "verification": ver_out,
            "clarification_question": str(view.get("question") or ""),
            "approval_requested": approval_requested,
            "pending_status": view.get("status"),
            "authorization": v.get("authorization") or {},
            "op_order": op_order,
            "op_authz_seen": bool(op_order) or bool(records),
            "trace_events": events,
            "escalation": run.get("escalation"),
            "edma_version": self.edma_version,
            "run_id": view.get("run_id"),
            "dop_path": view.get("dop_path") or [],
            "tool_capable": True,
            "status": view.get("status"),
        }
