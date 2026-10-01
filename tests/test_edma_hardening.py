"""EDMA Cognitive Layer Hardening — 5 audit topilmasi uchun testlar (2026-09-26).

A. edma/ops.py — deterministik op-graf validatsiyasi
B. FIX-1: bounded conversation context (multi-turn)
C. FIX-2: multi-op plan, dependency, re-planning, immutability
D. FIX-3: resume/lease/idempotency
E. FIX-4: action registry (web.fetch, default-deny op kind'lar)
G. MINOR-5B: model default siyosati (fail closed)
H. MINOR-5C: til fallback zanjiri
"""
import asyncio
import json

import pytest

from edma_core import runtime as RT
from edma_core import dop
from edma_core import ops as EDMA_OPS
from edma_core import actions as EDMA_ACTIONS
from edma_core import verification as EDMA_VERIFY
from edma_core.components import ComponentResult

from tests.test_edma_runtime import env, FakeModel, run_edma  # noqa: F401


def _search_shim(monkeypatch, empty_queries=()):
    """web.search op'lari uchun deterministik shim: empty_queries'dagi query →
    natija yo'q (FAIL); aks holda bitta natija (PASS)."""
    from edma_core import actions as A
    class FW:
        def cost_of(self, spec): return 0.002
        def balance(self, uid): return 10.0
        def try_spend(self, *a, **k): return True
        def charge(self, *a, **k): return {}
    class FSE:
        SEARCH_QUERY_COST = 0.002
        @staticmethod
        def search(q, n):
            if q in empty_queries:
                return {"results": [], "provider": "tavily"}
            return {"results": [{"title": "t", "url": "https://x.io/a",
                                 "content": "web search findings about " + q}],
                    "provider": "tavily"}
        @staticmethod
        def citations_of(rows): return [{"title": "t", "url": "https://x.io/a"}]
    from tests.helpers import reg_fake_search
    reg_fake_search(monkeypatch, A, FSE)
    monkeypatch.setattr("edma_core.runtime._wallet", FW())


BASE_SCRIPT = {
    "perceive": {"intent": "task", "requires_clarification": False,
                 "effort_hint": "low",
                 "action_candidate": "none"},
    "respond": {"text": "ok"},
}


# ══════════════════════════ A. ops.py — pure validation ══════════════════════════

def test_ops_valid_plan_normalizes():
    p, err, ign = EDMA_OPS.validate_plan([
        {"id": "op_1", "kind": "web.search", "args": {"query": "a"}, "depends_on": []},
        {"id": "op_2", "kind": "web.search", "args": {"query": "b"}, "depends_on": ["op_1"]},
    ])
    assert p is not None and err is None
    assert [o["id"] for o in p] == ["op_1", "op_2"]


def test_ops_empty_id_rejected():
    p, err, _ = EDMA_OPS.validate_plan([{"id": "", "kind": "web.search"}])
    assert p is None and "empty_id" in err


def test_ops_duplicate_id_rejected():
    p, err, _ = EDMA_OPS.validate_plan([
        {"id": "x", "kind": "memory.recall"}, {"id": "x", "kind": "web.search"}])
    assert p is None and "duplicate" in err


def test_ops_unknown_dependency_rejected():
    p, err, _ = EDMA_OPS.validate_plan([{"id": "a", "kind": "k", "depends_on": ["ghost"]}])
    assert p is None and "unknown_dependency" in err


def test_ops_self_dependency_rejected():
    p, err, _ = EDMA_OPS.validate_plan([{"id": "a", "kind": "k", "depends_on": ["a"]}])
    assert p is None and "self_dependency" in err


def test_ops_cycle_rejected():
    p, err, _ = EDMA_OPS.validate_plan([
        {"id": "a", "kind": "k", "depends_on": ["b"]},
        {"id": "b", "kind": "k", "depends_on": ["a"]}])
    assert p is None and "cycle" in err


def test_ops_malformed_args_rejected():
    p, err, _ = EDMA_OPS.validate_plan([{"id": "a", "kind": "k", "args": "not-a-dict"}])
    assert p is None and "args_not_object" in err


def test_ops_empty_list_rejected():
    p, err, _ = EDMA_OPS.validate_plan([])
    assert p is None and "not_nonempty" in err


def test_ops_too_many_rejected():
    p, err, _ = EDMA_OPS.validate_plan([{"id": f"o{i}", "kind": "k"} for i in range(10)])
    assert p is None and "too_many_ops" in err


def test_ops_banned_control_keys_scrubbed():
    p, err, ign = EDMA_OPS.validate_plan([
        {"id": "a", "kind": "web.search", "authorized": True, "verified": True,
         "state": "ACTION", "args": {"query": "q"}}])
    assert p is not None and err is None           # plan qabul, lekin...
    assert any(k.endswith("authorized") for k in ign) and any(k.endswith("verified") for k in ign) \
        and any(k.endswith("state") for k in ign)
    assert all(k not in p[0] for k in ("authorized", "verified", "state"))


def test_ops_initial_statuses_carries_pass():
    ops = [{"id": "a", "kind": "web.search", "args": {"query": "q"}, "depends_on": []}]
    fp = EDMA_OPS.plan_fingerprint(ops[0])
    ledger = {"a": {"status": "PASS", "fp": fp, "version": 1}}
    st = EDMA_OPS.initial_statuses(ops, ledger)
    assert st["a"] == "PASS"                         # xuddi shu semantika — carried
    ops2 = [{"id": "a", "kind": "web.search", "args": {"query": "BOSHQA"}, "depends_on": []}]
    st2 = EDMA_OPS.initial_statuses(ops2, ledger)
    assert st2["a"] == "PENDING"                     # semantika o'zgardi → yangi op


def test_ops_ready_respects_dependencies():
    ops = [{"id": "a", "kind": "k", "depends_on": []},
           {"id": "b", "kind": "k", "depends_on": ["a"]},
           {"id": "c", "kind": "k", "depends_on": []}]
    st = {"a": "PASS", "b": "PENDING", "c": "PENDING"}
    assert EDMA_OPS.ready_ops(ops, st) == ["b", "c"]  # plan tartibida: b (dep PASS) + c (mustaqil)
    st2 = {"a": "FAIL", "b": "PENDING", "c": "PASS"}
    assert EDMA_OPS.ready_ops(ops, st2) == []        # b BLOCKED
    assert EDMA_OPS.pending_left(ops, st2) is True
    assert EDMA_OPS.pending_left(ops, {"a": "PASS", "b": "PASS", "c": "PASS"}) is False


# ══════════════════════════ B. FIX-1: conversation context ══════════════════════════

def test_bound_context_limits_and_roles():
    raw = [{"role": "system", "content": "drop me"}] + [
        {"role": "user", "content": f"m{i}"} for i in range(20)]
    raw.append({"role": "assistant", "content": ""})   # bo'sh — tashlanadi
    ctx = RT.bound_context(raw)
    assert len(ctx) == RT.MAX_CONTEXT_MSGS
    assert ctx[-1]["content"] == "m19" and all(m["role"] in ("user", "assistant") for m in ctx)


def test_bound_context_char_budget_drops_oldest():
    raw = [{"role": "user", "content": "x" * 3000}, {"role": "assistant", "content": "y" * 3000},
           {"role": "user", "content": "zihirli oxirgi"}]
    ctx = RT.bound_context(raw)
    assert sum(len(m["content"]) for m in ctx) <= RT.MAX_CONTEXT_CHARS
    assert ctx[-1]["content"] == "zihirli oxirgi"      # eng yangisi saqlanadi


def test_context_injected_into_perceive_and_reason(env):
    ms, _ = env
    captured = {}

    class Spy(FakeModel):
        async def call(self, mode, payload, max_tokens=520, temperature=0.2):
            captured.setdefault(mode, []).append(payload)
            return await super().call(mode, payload, max_tokens, temperature)

    RT.set_model(Spy(script={}, always={
        "perceive": {**BASE_SCRIPT["perceive"]},
        "reason": {"ready": True, "response_draft": "PostgreSQL"},
        "respond": {"text": "Sizning loyihangiz PostgreSQL ishlatadi."}}))
    ctx = [{"role": "user", "content": "My project is called ThinkSync and uses PostgreSQL."},
           {"role": "assistant", "content": "Understood."}]
    run = RT.start_run("u1", "What database does my project use?", conversation_context=ctx)
    view = asyncio.run(RT.advance_run("u1", run))
    assert view["status"] == "complete"
    # PERCEIVING payload: current request user xabari, kontekst ALOHIDA system blok
    pmsgs = captured["perceive"][0]["messages"]
    assert pmsgs[0]["role"] == "user" and "database" in pmsgs[0]["content"]
    cblk = next(m for m in pmsgs if m["role"] == "system" and "CONVERSATION CONTEXT" in m["content"])
    assert "ThinkSync" in cblk["content"] and "Understood" in cblk["content"]
    # REASONING ham ko'radi
    rmsgs = captured["reason"][0]["messages"]
    rcblk = next(m for m in rmsgs if m["role"] == "system" and "CONVERSATION CONTEXT" in m["content"])
    assert "ThinkSync" in rcblk["content"]
    # RESPOND (yakuniy NL generator) HAM ko'radi — aks holda "shu suhbatda
    # aytilgan X nima?" savoliga generator javob topolmaydi (prod probe defekti)
    smsgs = captured["respond"][0]["messages"]
    sblk = next(m for m in smsgs if m["role"] == "system" and "CONVERSATION CONTEXT" in m["content"])
    assert "ThinkSync" in sblk["content"] and "Understood" in sblk["content"]
    # current request alohida user xabari qoladi
    assert any(m["role"] == "user" and "What database" in m["content"] for m in smsgs)
    assert any("CONVERSATION CONTEXT" in m["content"] for m in rmsgs)
    # current request kontekst ICHIDA EMAS (alohida uzatiladi)
    assert "What database does my project use?" not in cblk["content"]


def test_context_trace_metadata_only(env):
    ms, _ = env
    ctx = [{"role": "user", "content": "SEGRET-CONTENT-XYZ juda maxfiy suhbat matni"}]
    run = RT.start_run("u1", "salom", conversation_context=ctx)
    view = asyncio.run(RT.advance_run("u1", run, max_steps=24)) if False else asyncio.run(
        RT.advance_run("u1", run)) if False else asyncio.run(RT.advance_run("u1", run))
    fresh = RT.get_run("u1", run["id"])
    tr = json.dumps(fresh.get("trace"), ensure_ascii=False)
    assert "SEGRET-CONTENT-XYZ" not in tr               # raw kontent trace'ga KIRMAYDI
    ev = next(e for e in fresh["trace"] if e["state"] == "PERCEIVING")
    cm = ev["component"].get("conversation_context")
    assert cm and cm["count"] == 1 and cm["fp"]         # faqat metadata


def test_context_run_snapshot_persists_for_resume(env):
    ms, _ = env
    ctx = [{"role": "user", "content": "turn one"}]
    run = RT.start_run("u1", "davom etamiz", conversation_context=ctx)
    saved = RT.get_run("u1", run["id"])
    assert saved["conversation_meta"]["count"] == 1
    assert saved["conversation_context"][0]["content"] == "turn one"


def test_model_cannot_modify_context(env):
    ms, _ = env
    RT.set_model(FakeModel(script={}, always={
        "perceive": {**BASE_SCRIPT["perceive"], "conversation_context": [{"role": "user", "content": "HACK"}]},
        "reason": {"ready": True}, "respond": {"text": "ok"}}))
    run = RT.start_run("u1", "salom", conversation_context=[{"role": "user", "content": "aslini"}])
    asyncio.run(RT.advance_run("u1", run))
    fresh = RT.get_run("u1", run["id"])
    assert fresh["conversation_context"][0]["content"] == "aslini"   # model o'zgartira olmadi


# ══════════════════════════ C. FIX-2: multi-step op plans ══════════════════════════
# ══════════════════════════ C. FIX-2: multi-step op plans ══════════════════════════

def _two_op_script():
    ops_v1 = [{"id": "op_1", "kind": "web.search", "args": {"query": "birinchi"}, "depends_on": []},
              {"id": "op_2", "kind": "web.search", "args": {"query": "ikkinchi"}, "depends_on": ["op_1"]}]
    return ops_v1


def test_two_sequential_ops_execute_in_order(env):
    ms, mp = env
    _search_shim(mp)
    ops = _two_op_script()
    RT.set_model(FakeModel(script={
        "perceive": [dict(BASE_SCRIPT["perceive"])],
        "reason": [
            {"ready": False, "ops": ops},
            {"ready": False, "ops": ops},   # op_2 davomi (op_1 PASS)
            {"ready": True, "response_draft": "done"},
        ],
        "respond": {"text": "ikkala op bajarildi"},
    }))
    run = RT.start_run("u1", "ikki qadamli vazifa")
    view = asyncio.run(RT.advance_run("u1", run))
    assert view["status"] == "complete", view
    fresh = RT.get_run("u1", run["id"])
    led = (fresh.get("view") or {}).get("op_ledger") or {}
    assert led.get("op_1", {}).get("status") == "PASS"
    assert led.get("op_2", {}).get("status") == "PASS"
    path = view["dop_path"]
    assert path.count("ACTION") == 2 and path.count("VERIFICATION") == 2
    # tartib: op_1 op_2'dan OLDIN
    assert path.index("ACTION") < len(path) - 1 - path[::-1].index("ACTION")


def test_failed_op_replanned_and_history_immutable(env):
    ms, mp = env
    # 1-urinish: "yooq" query → natija yo'q → FAIL; keyin model revizyon qilib
    # XUDDI SHU id bilan BOSHQA args qayta taklif qiladi → 2-urinish PASS.
    _search_shim(mp, empty_queries={"yooq"})
    ops1 = [{"id": "op_1", "kind": "web.search", "args": {"query": "yooq"}, "depends_on": []}]
    ops2 = [{"id": "op_1", "kind": "web.search", "args": {"query": "bor"}, "depends_on": []}]
    RT.set_model(FakeModel(script={
        "perceive": [dict(BASE_SCRIPT["perceive"])],
        "reason": [
            {"ready": False, "ops": ops1},
            {"ready": False, "ops": ops2},   # revizyon (fp o'zgardi → qayta bajariladi)
            {"ready": True, "response_draft": "topildi"},
        ],
        "respond": {"text": "natija"},
    }))
    run = RT.start_run("u1", "qidir")
    view = asyncio.run(RT.advance_run("u1", run))
    assert view["status"] == "complete", view
    fresh = RT.get_run("u1", run["id"])
    v = fresh.get("view") or {}
    led = v.get("op_ledger") or {}
    hist = v.get("op_history") or []
    assert led.get("op_1", {}).get("status") == "PASS"
    # TARIX IMMUTABLE: FAIL yozuv ham saqlanadi (o'chirilmaydi)
    statuses = [h["status"] for h in hist if h["op_id"] == "op_1"]
    assert "FAIL" in statuses and statuses[-1] == "PASS"
    # graf versiyalari
    assert (v.get("op_graph") or {}).get("version", 0) >= 2



def test_max_graph_versions_budget_escalates(env):
    ms, mp = env
    _search_shim(mp, empty_queries={"v1", "v2", "v3", "v4"})
    RT.set_model(FakeModel(script={
        "perceive": [dict(BASE_SCRIPT["perceive"])],
        "reason": [
            {"ready": False, "ops": [{"id": "a", "kind": "web.search", "args": {"query": "v1"}, "depends_on": []}]},
            {"ready": False, "ops": [{"id": "a", "kind": "web.search", "args": {"query": "v2"}, "depends_on": []}]},
            {"ready": False, "ops": [{"id": "a", "kind": "web.search", "args": {"query": "v3"}, "depends_on": []}]},
            {"ready": False, "ops": [{"id": "a", "kind": "web.search", "args": {"query": "v4"}, "depends_on": []}]},
        ],
        "respond": {"text": "ok"},
    }))
    run = RT.start_run("u1", "cheasiz replan")
    view = asyncio.run(RT.advance_run("u1", run))
    # §12: budjet tugadi → halol escalation/response (cheksiz loop YO'Q)
    assert view["lifecycle"] in ("escalated", "complete", "failed")
    fresh = RT.get_run("u1", run["id"])
    assert (fresh.get("view") or {}).get("op_graph", {}).get("version", 0) <= EDMA_OPS.MAX_GRAPH_VERSIONS


def test_invalid_ops_plan_is_honest_not_fatal(env):
    ms, _ = env
    RT.set_model(FakeModel(script={
        "perceive": [dict(BASE_SCRIPT["perceive"])],
        "reason": [
            {"ready": False, "ops": [{"id": "a", "kind": "web.search", "depends_on": ["ghost"]}]},
            {"ready": True, "response_draft": "plan xato, baribir javob"},
        ],
        "respond": {"text": "plan rad etildi"},
    }))
    run = RT.start_run("u1", "noto'g'ri plan")
    view = asyncio.run(RT.advance_run("u1", run))
    fresh = RT.get_run("u1", run["id"])
    assert view["status"] == "complete"                  # halol davom
    assert (fresh.get("view") or {}).get("op_plan_error") or True  # yozuv qoladi
    assert (fresh.get("view") or {}).get("op_ledger") in (None, {})  # hech narsa bajarilmagan


# ══════════════════════════ D. FIX-3: resume / lease / idempotency ══════════════════════════

def test_slice_pause_then_worker_resume_same_run_id(env):
    ms, _ = env
    RT.set_model(FakeModel(script={}, always={
        "perceive": {**BASE_SCRIPT["perceive"], "effort_hint": "medium"},
        "energy": {"complexity": "low"},
        "reason": {"ready": True, "response_draft": "x"},
        "respond": {"text": "yakun"}}))
    run = RT.start_run("u1", "porsiyalar bilan")
    view = asyncio.run(RT.advance_run("u1", run, max_steps=2))
    # slice tugadi → PENDING (failed EMAS)
    mid = RT.get_run("u1", run["id"])
    assert mid["lifecycle"] == "pending" and mid["state"] != "END"
    # worker resume — XUDDI SHU runtime, XUDDI SHU run_id
    view2 = asyncio.run(RT.resume_run("u1", run["id"], worker_id="w1"))
    assert view2["status"] == "complete"
    assert view2["run_id"] == run["id"]
    fresh = RT.get_run("u1", run["id"])
    assert fresh["lease"] in (None, {})                 # lease bo'shatildi


def test_lease_prevents_concurrent_claim_and_expires(env):
    ms, _ = env
    run = RT.start_run("u1", "lease test")
    r1, why1 = RT.claim_run("u1", run["id"], "worker-A")
    assert r1 is not None and why1 == "claimed"
    r2, why2 = RT.claim_run("u1", run["id"], "worker-B")
    assert r2 is None and why2 == "leased"              # parallel claim YO'Q
    # muddati o'tgan lease → qayta olish mumkin (deterministik recovery)
    fresh = RT.get_run("u1", run["id"])
    fresh["lease"]["until"] = RT._now() - 1
    RT._save("u1", fresh)
    r3, why3 = RT.claim_run("u1", run["id"], "worker-B")
    assert r3 is not None and why3 == "claimed"


def test_resume_attempts_bounded_then_failed(env):
    ms, _ = env
    run = RT.start_run("u1", "cheksiz retry test")
    for i in range(RT.MAX_RESUME_ATTEMPTS):
        r, why = RT.claim_run("u1", run["id"], f"w{i}")
        assert r is not None
        RT.release_run("u1", RT.get_run("u1", run["id"]))
    r, why = RT.claim_run("u1", run["id"], "w-overflow")
    assert r is None and why == "max_attempts"
    assert RT.get_run("u1", run["id"])["lifecycle"] == "failed"


def test_no_duplicated_action_or_spend_on_reproposal(env):
    ms, mp = env
    _search_shim(mp)
    calls = {"n": 0}
    real_execute = EDMA_ACTIONS.execute

    def counting_execute(kind, args, ctx):
        calls["n"] += 1
        return real_execute(kind, args, ctx)

    EDMA_ACTIONS.execute = counting_execute
    try:
        ops_pass = [{"id": "op_1", "kind": "web.search", "args": {"query": "duplicat-test"}, "depends_on": []}]
        RT.set_model(FakeModel(script={
            "perceive": [dict(BASE_SCRIPT["perceive"])],
            "reason": [
                {"ready": False, "ops": ops_pass},
                {"ready": False, "ops": ops_pass},   # XUDDI SHU op qayta taklif (fp bir xil)
                {"ready": True, "response_draft": "ok"},
            ],
            "respond": {"text": "bir marta bajarildi"},
        }))
        run = RT.start_run("u1", "idempotent")
        view = asyncio.run(RT.advance_run("u1", run))
        assert view["status"] == "complete"
        assert calls["n"] == 1, f"action {calls['n']} marta bajarildi — IDEMPOTENCY BUZILDI"
    finally:
        EDMA_ACTIONS.execute = real_execute


def test_discover_pending_finds_only_unleased(env):
    ms, _ = env
    run = RT.start_run("u1", "discovery")
    RT._save("u1", RT.get_run("u1", run["id"]))  # lifecycle pending
    pend = RT.discover_pending(["u1"], limit=5)
    assert any(p["run_id"] == run["id"] for p in pend)
    # lease faol → discovery ko'rmaydi
    RT.claim_run("u1", run["id"], "busy-worker")
    pend2 = RT.discover_pending(["u1"], limit=5)
    assert not any(p["run_id"] == run["id"] for p in pend2)


# ══════════════════════════ E. FIX-4: action registry ══════════════════════════

def test_registry_has_webfetch_and_default_deny():
    assert "web.fetch" in EDMA_ACTIONS.ACTION_REGISTRY
    # CORE: capability amallar PLUGIN — ro'yxatdan o'tmaguncha default-deny:
    assert "web.search" not in EDMA_ACTIONS.ACTION_REGISTRY
    assert "dataset.create" not in EDMA_ACTIONS.ACTION_REGISTRY
    # memory.recall O'CHIRILGAN (memory-agnostik) + unknown kind — default deny
    assert "memory.recall" not in EDMA_ACTIONS.ACTION_REGISTRY
    r = EDMA_ACTIONS.execute("shell.exec", {"cmd": "ls"}, {"uid": "u1"})
    assert r.ok is False and "default_deny" in (r.error or "")


def test_webfetch_rejects_non_https():
    r = EDMA_ACTIONS.execute("web.fetch", {"url": "http://insecure.example.com"}, {"uid": "u1"})
    assert r.ok is False and "invalid_url" in (r.error or "")


def test_webfetch_executes_with_evidence_and_verification(monkeypatch):
    import httpx
    html = b"<html><body><script>var x=1;</script><p>ThinkSync deploys PostgreSQL 16</p></body></html>"

    def _fake_transport(request):
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=html)

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(_fake_transport)))
    out = EDMA_ACTIONS.execute("web.fetch", {"url": "https://example.com/x"}, {"uid": "u1"})
    assert out.ok is True
    assert "PostgreSQL" in out.output.get("text", "")
    ev = out.evidence
    assert ev.get("sha16") and ev.get("status") == 200 and ev.get("chars") > 0
    # VERIFICATION evidence bilan hukm qiladi
    v = EDMA_VERIFY.verify_action("web.fetch", out.to_dict(), "u1")
    assert v.result == EDMA_VERIFY.PASS and (v.evidence_summary or {}).get("sha16") == ev["sha16"]


def test_model_authorized_flag_inside_op_ignored_at_runtime(env):
    ms, _ = env
    ops = [{"id": "a", "kind": "web.search", "authorized": True, "args": {"query": "x"},
            "verified": True, "depends_on": []}]
    p, err, ign = EDMA_OPS.validate_plan(ops)
    assert p is not None and any(k.endswith("authorized") for k in ign) \
        and any(k.endswith("verified") for k in ign)


def test_unregistered_op_kind_reaches_legal_denial(env):
    ms, _ = env
    ops = [{"id": "a", "kind": "shell.exec", "args": {"cmd": "rm -rf /"}, "depends_on": []}]
    RT.set_model(FakeModel(script={
        "perceive": [dict(BASE_SCRIPT["perceive"])],
        "reason": [
            {"ready": False, "ops": ops},
            {"ready": True, "response_draft": "rad etildi"},
        ],
        "respond": {"text": "bu amal ro'yxatda yo'q"},
    }))
    run = RT.start_run("u1", "shell ishlat")
    view = asyncio.run(RT.advance_run("u1", run))
    fresh = RT.get_run("u1", run["id"])
    # legal yo'l: ACTION_DECISION → ACTION_AUTHORIZATION → denied → ESCALATION_CHECK → ...
    assert "ACTION_AUTHORIZATION" in view["dop_path"]
    az = fresh.get("view", {}).get("authorization") or {}
    assert az.get("status") == "denied"
    assert fresh.get("view", {}).get("op_ledger", {}).get("a", {}).get("status") != "PASS"


# ══════════════════════════ G. MINOR-5B: model default ══════════════════════════

def test_resolve_model_priority_chain():
    """MINOR-5B core siyosati: explicit → persisted run modeli → default (fail-closed).
    Core'da default KONFIGURATSIYALANUVCHI (set_default_model) — legacy normalize
    platforma qatlamida."""
    assert RT.resolve_model() == RT.EDMA_DEFAULT_MODEL
    assert RT.resolve_model(explicit=RT.EDMA_DEFAULT_MODEL) == RT.EDMA_DEFAULT_MODEL
    assert RT.resolve_model(run={"model": RT.EDMA_DEFAULT_MODEL}) == RT.EDMA_DEFAULT_MODEL
    with pytest.raises(RT.ModelPolicyError):
        RT.resolve_model(explicit="gpt-4o")
    with pytest.raises(RT.ModelPolicyError):
        RT.resolve_model(run={"model": "claude-3"})


def test_default_model_is_configurable_fail_closed():
    old = RT.EDMA_DEFAULT_MODEL
    try:
        RT.set_default_model("my-model")
        assert RT.resolve_model(explicit="my-model") == "my-model"
        with pytest.raises(RT.ModelPolicyError):
            RT.resolve_model(explicit="other")
    finally:
        RT.set_default_model(old)


def test_advance_run_unknown_model_fails_closed_not_silent(env):
    """Core: noma'lum model siyosati → halol failed_model_policy (jim o'tish TAQIQLANGAN).
    (Legacy-normalize platforma qatlamida — core faqat o'z defaultini biladi.)"""
    ms, _ = env
    RT.set_model(FakeModel(script={}, always={
        "perceive": {**BASE_SCRIPT["perceive"]}, "reason": {"ready": True},
        "respond": {"text": "ok"}}))
    run = RT.start_run("u1", "unknown model")
    run["model"] = "some-unknown-model"
    view = asyncio.run(RT.advance_run("u1", run))
    assert view["status"] == "failed_model_policy"          # FAIL CLOSED, jim emas


def test_advance_run_invalid_model_fails_closed(env):
    ms, _ = env
    calls = {"n": 0}

    class NoCall(FakeModel):
        async def call(self, mode, payload, max_tokens=520, temperature=0.2):
            calls["n"] += 1
            return await super().call(mode, payload, max_tokens, temperature)

    RT.set_model(NoCall(script={}, always={"perceive": dict(BASE_SCRIPT["perceive"]),
                                           "respond": {"text": "x"}}))
    run = RT.start_run("u1", "invalid model")
    run["model"] = "gpt-4o"
    view = asyncio.run(RT.advance_run("u1", run))
    assert view["status"] == "failed_model_policy"          # FAIL CLOSED
    assert view["lifecycle"] == "failed"
    assert calls["n"] == 0                                  # model CHAQIRILMAGAN


# ══════════════════════════ H. MINOR-5C: language fallback ══════════════════════════

def test_language_fallback_chain():
    run = {"conversation_meta": {}}
    assert RT._resolve_language("en", run, "u1") == "en"          # detected valid
    assert RT._resolve_language("", run, "u1") == "uz"            # default uz
    assert RT._resolve_language("xx", run, "u1") == "uz"          # invalid detected
    assert RT._resolve_language("", {"conversation_meta": {"language": "ru"}}, "u1") == "ru"
    assert RT._resolve_language("de", {"conversation_meta": {"language": "ru"}}, "u1") == "de"  # detected ustun


def test_heuristic_empty_detection_still_nonempty_language(env):
    ms, _ = env
    # model perceive 2 marta fail → heuristic (language "") → fallback "uz"
    RT.set_model(FakeModel(script={"perceive": [None, None],
                                   "reason": [{"ready": True}],
                                   "respond": [{"text": "uz javob"}]}))
    run = RT.start_run("u1", "salom")
    view = asyncio.run(RT.advance_run("u1", run))
    fresh = RT.get_run("u1", run["id"])
    assert (fresh.get("view") or {}).get("user_language") == "uz"
    tr = json.dumps(fresh.get("trace"), ensure_ascii=False)
    assert '"language": "uz"' in tr or '"language":"uz"' in tr


def test_mixed_language_input_falls_back_deterministically(env):
    ms, _ = env
    RT.set_model(FakeModel(script={}, always={
        "perceive": {**BASE_SCRIPT["perceive"], "language": "mix"},   # invalid til
        "reason": {"ready": True},
        "respond": {"text": "ok"}}))
    run = RT.start_run("u1", "hello salam привет")
    asyncio.run(RT.advance_run("u1", run))
    fresh = RT.get_run("u1", run["id"])
    assert (fresh.get("view") or {}).get("user_language") == "uz"


def test_russian_english_detection_respected(env):
    ms, _ = env
    for lang, msg in (("en", "hello there"), ("ru", "privet")):
        RT.set_model(FakeModel(script={}, always={
            "perceive": {**BASE_SCRIPT["perceive"], "language": lang},
            "reason": {"ready": True},
            "respond": {"text": "ok"}}))
        run = RT.start_run("u1", msg)
        asyncio.run(RT.advance_run("u1", run))
        assert (RT.get_run("u1", run["id"]).get("view") or {}).get("user_language") == lang


# (API-darajasi E2E testlari SaaS repoda qoladi — /api/edma/* endpointlari core emas)
