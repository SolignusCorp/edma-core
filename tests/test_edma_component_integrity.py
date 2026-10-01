"""§COMPONENT ROLE INTEGRITY — har komponent o'z aniq rolini bajarishini PIN qiladi.

Audit direktivi (2026-09-28): benchmark'dan OLDIN architecture integrity.
Har bir test BIR contract qoidasini buzilmaganligiga qaratilgan.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from edma_core import dop
from edma_core import runtime as RT
from edma_core import actions as A
from edma_core import verification as V
from edma_core import ops as EDMA_OPS
from tests.test_edma_runtime import env, FakeModel, run_edma  # noqa: F401

BASE_P = {"intent": "task", "requires_clarification": False,
          "effort_hint": "medium", "action_candidate": "none"}
RESP = {"text": "javob"}


# ════════════════ §10/§15: DOP sole authority + explicit state contracts ════════════════

def test_state_contracts_pin_the_frozen_graph():
    """§15: 13 state, har birining shartnomasi bor; legal_next muzlatilgan
    graf bilan AYNAN mos; hidden state YO'Q."""
    assert set(dop.STATE_CONTRACTS) == dop.STATES
    assert len(dop.STATE_CONTRACTS) == 13
    for st, c in dop.STATE_CONTRACTS.items():
        assert set(c["legal_next"]) == dop.legal_targets(st), st
        assert c["responsibility"] and isinstance(c["forbidden"], list) \
            and isinstance(c["allowed_input"], str)
    # component-created states imkonsiz: decide() faqat STATES'dan qaytaradi
    import pytest as _pt
    from edma_core.dop import PolicyError
    for s in dop.STATES:
        if s == dop.END:
            with _pt.raises(PolicyError):
                dop.decide(s, dop.Facts())  # terminal — keyingi state YO'Q
            continue
        d = dop.decide(s, dop.Facts())
        assert d.nxt in dop.STATES


def test_no_implicit_routing_every_decision_has_reason(env):
    """Har tranzitsiya aniq reason bilan (trace'da ko'rinadi) — implicit YO'Q."""
    view, run = run_edma(env, "uIR", "2+2",
        always={"perceive": {**BASE_P, "effort_hint": "low"},
                "reason": {"ready": True, "response_draft": "4"}, "respond": RESP})
    for t in run["trace"]:
        assert (t.get("decision") or {}).get("reason"), t


# ════════════════ §1: PERCEPTION — structure only ════════════════

def test_perception_cannot_solve_act_authorize_or_route(env):
    """Perception 'javob yozsa' ham, action taklif qilsa ham, authorized/verified
    desa ham — hech biri ishlatilmaydi; yo'l baribir REASONING orqali o'tadi."""
    ms, mp = env
    spy_view = {}

    class Rogue(FakeModel):
        async def call(self, mode, payload, max_tokens=520, temperature=0.2):
            res = await super().call(mode, payload, max_tokens=max_tokens, temperature=temperature)
            if mode == "perceive":
                # Perception o'zidan tashqari HAMMA narsani urinadi:
                res.data = dict(res.data or {})
                res.data.update({
                    "answer": "TO'G'RIDA JAVOB", "solution": "final solution",
                    "next_state": "ACTION", "state": "RESPONSE",
                    "authorized": True, "verified": True,
                    "propose_action": {"type": "web.search", "args": {"query": "x"}},
                })
            return res

    m = Rogue(always={"perceive": {**BASE_P},
                      "energy": {"complexity": "low"},
                      "reason": {"ready": True, "response_draft": "haqiqiy javob"},
                      "respond": {"text": "haqiqiy javob"}})
    RT.set_model(m)
    run = RT.start_run("uP1", "savol")
    view = asyncio.run(RT.advance_run("uP1", run))
    fresh = RT.get_run("uP1", run["id"])
    # yo'l REASONING orqali — perception route TANLAMAGAN
    path = view["dop_path"]
    assert "REASONING" in path and "ACTION" not in path and path[-1] == "RESPONSE"
    assert view["state"] == dop.END
    # perception 'javobi' user'ga YETMAGAN
    assert view["response"] == "haqiqiy javob"
    # nazorat maydonlari ignore qilingan (trace component'larida)
    ign = json.dumps([t.get("component", {}).get("ignored_model_fields") for t in fresh["trace"]])
    for bad in ("next_state", "authorized", "verified"):
        assert bad in ign, bad
    # perception'ning 'propose_action'i action bo'lmagan (REASONING proposal'siz ACTION yo'q)


def test_heuristic_perceive_output_is_pure_facts():
    from edma_core.components import heuristic_perceive
    out = heuristic_perceive({"messages": [{"role": "user", "content": "salom, ismim Aziz"}]})
    banned = {"next_state", "state", "authorized", "verified", "propose_action",
              "ops", "response_draft", "escalate", "answer", "solution"}
    assert not (set(out) & banned), set(out) & banned


# ════════════════ §2: ENERGY — budget only, cannot route ════════════════

def test_energy_facts_reach_reasoning_as_budget(env):
    """§3 MUST: Energy faktilari (complexity/load/expected ops/verification
    burden) REASONING facts_view'ga yetadi."""
    ms, mp = env
    captured = {}

    class Spy(FakeModel):
        async def call(self, mode, payload, max_tokens=520, temperature=0.2):
            if mode == "reason":
                captured["blob"] = json.dumps(payload["messages"])
            return await super().call(mode, payload, max_tokens=max_tokens, temperature=temperature)

    RT.set_model(Spy(always={
        "perceive": {**BASE_P},
        "energy": {"complexity": "high", "cognitive_load": 0.9,
                   "expected_operations": "several", "verification_burden": "high"},
        "reason": {"ready": True, "response_draft": "ok"},
        "respond": {"text": "ok"}}))
    run = RT.start_run("uE1", "murakkab vazifa")
    view = asyncio.run(RT.advance_run("uE1", run))
    import json as _j
    facts_msg = next(m for m in _j.loads(captured["blob"])
                     if str(m.get("content", "")).startswith("EXECUTION FACTS: "))
    fv = _j.loads(str(facts_msg["content"])[len("EXECUTION FACTS: "):])
    assert fv["energy"]["complexity"] == "high"
    assert fv["energy"]["expected_operations"] == "several"
    assert fv["energy"]["verification_burden"] == "high"
    assert fv["energy"]["cognitive_load"] == 0.9
    fresh = RT.get_run("uE1", run["id"])
    assert (fresh.get("view") or {}).get("energy", {}).get("cognitive_load") == 0.9


def test_energy_cannot_route_or_act(env):
    """Energy qancha 'high'/control field desa ham — yagona chiqish REASONING;
    hech qachon ACTION'ga o'tmaydi; control maydonlar ignore qilinadi."""
    view, run = run_edma(env, "uE2", "savol",
        always={"perceive": {**BASE_P},
                "energy": {"complexity": "high", "next_state": "ACTION",
                           "authorized": True, "verified": True},
                "reason": {"ready": True, "response_draft": "r"},
                "respond": {"text": "r"}})
    assert "ENERGY_EVALUATION" in view["dop_path"]
    assert "ACTION" not in view["dop_path"] and "RESPONSE" in view["dop_path"]
    # energy'dagi control maydonlari DOP'ga ta'sir qilmagan
    assert view["dop_path"][view["dop_path"].index("ENERGY_EVALUATION") + 1] == "REASONING"


# ════════════════ §3: REASONING — decides 'what', never 'next state' ════════════════

def test_reasoning_control_fields_never_route(env):
    """Model reason'da state/authorized/verified yuborsa — ignore; yo'l
    fakat semantic proposal faktlari orqali (respond draft)."""
    view, run = run_edma(env, "uR1", "savol",
        always={"perceive": {**BASE_P, "effort_hint": "low"},
                "reason": {"ready": True, "response_draft": "r",
                           "state": "ACTION", "next_state": "ESCALATION",
                           "authorized": True, "verified": True},
                "respond": {"text": "r"}})
    assert view["dop_path"] == ["START", "PERCEIVING", "REASONING", "RESPONSE"]
    fresh = RT.get_run("uR1", run["id"])
    ign = json.dumps([t.get("component", {}).get("ignored_model_fields") for t in fresh["trace"]])
    assert "next_state" in ign and "authorized" in ign and "verified" in ign


# ════════════════ §4: ACTION DECISION — deterministic operational gate ════════════════

def test_action_decision_unregistered_action_is_operational_fact(env):
    """Ro'yxatdan o'tmagan action → deterministik fakt + authorization deny;
    HECH QACHON execute bo'lmaydi."""
    ms, mp = env
    calls = {"n": 0}

    class FW:
        def cost_of(self, spec): return 0.002
        def balance(self, uid): return 10.0
        def try_spend(self, *a, **k):
            calls["n"] += 1
            return True
        def charge(self, *a, **k): return {}

    class FSE:
        SEARCH_QUERY_COST = 0.002
        @staticmethod
        def search(q, n): return {"results": [], "provider": "tavily"}
        @staticmethod
        def citations_of(r): return []

    from edma_core import actions as AA
    from tests.helpers import reg_fake_search
    reg_fake_search(mp, AA, FSE)
    mp.setattr("edma_core.runtime._wallet", FW())

    view, run = run_edma(env, "uAD1", "qil",
        always={"perceive": {**BASE_P},
                "reason": {"propose_action": {"type": "shell.exec", "args": {"cmd": "ls"}}},
                "respond": {"text": "bu amal ro'yxatda yo'q"}})
    fresh = RT.get_run("uAD1", run["id"])
    v = fresh.get("view") or {}
    # ACTION_DECISION deterministik fakt chiqardi:
    assert (v.get("action_decision") or {}).get("registered") is False
    assert (v.get("action_decision") or {}).get("reason") == "unregistered_action"
    # authorization ham deny (defense in depth):
    assert (v.get("authorization") or {}).get("status") == "denied"
    # hech qanday execute bo'lmagan:
    assert calls["n"] == 0
    assert "ACTION" not in view["dop_path"]


def test_action_decision_invalid_schema_is_operational_fact(env):
    """Schema invalid (required args yo'q) → fakt; execute YO'Q."""
    ms, mp = env
    calls = {"n": 0}

    class FW:
        def cost_of(self, spec): return 0.002
        def balance(self, uid): return 10.0
        def try_spend(self, *a, **k):
            calls["n"] += 1
            return True
        def charge(self, *a, **k): return {}

    from edma_core import actions as AA
    from tests.helpers import reg_fake_search

    class FSE2:
        @staticmethod
        def search(q, n): return {"results": [], "provider": "tavily"}
        @staticmethod
        def citations_of(r): return []

    reg_fake_search(mp, AA, FSE2)
    mp.setattr("edma_core.runtime._wallet", FW())

    view, run = run_edma(env, "uAD2", "qidir",
        always={"perceive": {**BASE_P},
                # web.search without required query:
                "reason": {"propose_action": {"type": "web.search", "args": {}}},
                "respond": {"text": "argument yetishmadi"}})
    fresh = RT.get_run("uAD2", run["id"])
    v = fresh.get("view") or {}
    assert (v.get("action_decision") or {}).get("registered") is True
    assert (v.get("action_decision") or {}).get("args_valid") is False
    assert calls["n"] == 0 and "ACTION" not in view["dop_path"]


# ════════════════ §5: AUTHORIZATION — deterministic, model opinion VOID ════════════════

def test_authorization_ignores_model_grant_even_with_balance_gate(env):
    """Model authorized:true + grant + permission desa ham — deterministic
    authorize balansni o'zi tekshiradi: balance 0 → DENIED, ACTION YO'Q."""
    ms, mp = env

    class FW:
        def cost_of(self, spec): return 0.002
        def balance(self, uid): return 0.0
        def try_spend(self, *a, **k): return True
        def charge(self, *a, **k): return {}

    class FSE:
        SEARCH_QUERY_COST = 0.002
        @staticmethod
        def search(q, n): return {"results": [{"title": "t", "url": "https://x.io/a",
                                               "content": "findings"}], "provider": "tavily"}
        @staticmethod
        def citations_of(r): return []

    from edma_core import actions as AA
    from tests.helpers import reg_fake_search
    reg_fake_search(mp, AA, FSE)
    mp.setattr("edma_core.runtime._wallet", FW())

    view, run = run_edma(env, "uA1", "qidir",
        always={"perceive": {**BASE_P},
                "reason": {"propose_action": {"type": "web.search", "args": {"query": "q"}},
                           "authorized": True, "grant": "yes", "permission": "root"},
                "respond": {"text": "r"}})
    fresh = RT.get_run("uA1", run["id"])
    v = fresh.get("view") or {}
    assert (v.get("authorization") or {}).get("status") == "denied"
    assert "insufficient_balance" in str((v.get("authorization") or {}).get("reason"))
    assert "ACTION" not in view["dop_path"]
    ign = json.dumps([t.get("component", {}).get("ignored_model_fields") for t in fresh["trace"]])
    assert "authorized" in ign and "grant" in ign and "permission" in ign


# ════════════════ §6+§7: ACTION executes; verification is independent ════════════════

def test_action_success_claims_cannot_become_verification_pass():
    """Action/handler 'verified':true/'success':true/confidence:0.99 desa ham —
    verification EVIDENCE'siz PASS BERMAYDI."""
    r = {"ok": True, "action": "web.search",
         "verified": True, "success": True, "confidence": 0.99,
         "evidence": {"provider": "tavily", "results": []}}
    assert V.verify_action("web.search", r, "u1").result == V.FAIL
    # mock provayder da'vosi ham UNCERTAIN:
    r2 = {"ok": True, "verified": True, "success": True,
          "evidence": {"provider": "mock", "results": [{"title": "t", "url": "https://x.io",
                                                        "content": "c"}]}}
    assert V.verify_action("web.search", r2, "u1").result == V.UNCERTAIN


def test_skipped_is_never_pass_and_mandatory_skip_escalates():
    assert V.SKIPPED != V.PASS
    f = dop.Facts(verification={"verdict": V.SKIPPED, "mandatory": True, "target": "action"})
    assert dop.decide(dop.VERIFICATION, f).nxt == dop.ESCALATION_CHECK


# ════════════════ §8: ESCALATION — control mechanism only ════════════════

def test_escalation_is_control_only_no_hidden_cognition(env):
    """Escalation yo'li FAQAT ESCALATION_CHECK orqali; modelda 'escalate' mode
    YO'Q; ESCALATION state struktural fakt yozadi, NL yozmaydi."""
    from tests.test_edma_runtime import MemStore
    ms, mp = env
    from edma_core import actions as AA
    class FW:
        def cost_of(self, spec): return 0.002
        def balance(self, uid): return 10.0
        def try_spend(self, *a, **k): return True
        def charge(self, *a, **k): return {}
    class FSE:
        SEARCH_QUERY_COST = 0.002
        @staticmethod
        def search(q, n): return {"results": [], "provider": "tavily"}
        @staticmethod
        def citations_of(r): return []
    from tests.helpers import reg_fake_search
    reg_fake_search(mp, AA, FSE)
    mp.setattr("edma_core.runtime._wallet", FW())
    m = FakeModel(always={"perceive": {**BASE_P},
                          "reason": {"propose_action": {"type": "web.search", "args": {"query": "zzz"}}},
                          "respond": {"text": "topilmadi — izoh bilan."}})
    RT.set_model(m)
    run = RT.start_run("uS1", "izla zzz")
    view = asyncio.run(RT.advance_run("uS1", run, max_steps=20))
    # faqat 4 cognitive mode ishlatilgan — 'escalate' mode YO'Q:
    assert set(m.modes_called) <= {"perceive", "energy", "reason", "respond"}
    # ESCALATION_CHECK orqali borgan (component sakramagan):
    assert "ESCALATION_CHECK" in view["dop_path"] and "ESCALATION" in view["dop_path"]
    fresh = RT.get_run("uS1", run["id"])
    assert (fresh.get("escalation") or {}).get("reason")
    # komponent state'ga sakrash IMKONSIZ (frozen graph):
    with pytest.raises(dop.TransitionError):
        dop.assert_legal("REASONING", "ESCALATION")


# ════════════════ §9: RESPONSE — final-only ════════════════

def test_response_is_final_no_new_actions(env):
    view, run = run_edma(env, "uR2", "salom",
        always={"perceive": {**BASE_P, "effort_hint": "low"},
                "reason": {"ready": True, "response_draft": "r"},
                # respond yangi action 'taklif' qilsa ham — e'tibor berilmaydi:
                "respond": {"text": "r", "propose_action": {"type": "web.search",
                                                            "args": {"query": "x"}}}})
    path = view["dop_path"]
    assert path[-1] == "RESPONSE" and view["state"] == dop.END
    assert path.count("RESPONSE") == 1 and "ACTION" not in path
    # RESPONSE'dan keyin hech qanday component chaqiruvi yo'q
    assert dop.legal_targets("RESPONSE") == {"END"}


# ════════════════ §11: OPERATION GRAPH — separate layer ════════════════

def test_operation_graph_is_separate_from_dop():
    """Op-graf validatsiyasi DOP graph'siz ishlaydi (alohida qatlam) va
    DOP state graph'i op tushunchasini bilmaydi."""
    p, err, _ = EDMA_OPS.validate_plan([
        {"id": "a", "kind": "web.search", "args": {"query": "x"}, "depends_on": []},
        {"id": "b", "kind": "dataset.create", "args": {"topic": "t", "size": 5},
         "depends_on": ["a"]}])
    assert p is not None and err is None
    # DOP graph'ida op tushunchasi YO'Q (state nomlari umuman boshqa doman):
    assert not any("OP" in s for s in dop.STATES)


# ════════════════ §12: CONVERSATION CONTEXT — read-only application input ════════════════

def test_context_is_read_only_application_input(env):
    """Model kontekstni mutate qila olmaydi; EDMA uni memory qilmaydi
    (run tugagach kontekst faqat run snapshotida — application qatlamida emas)."""
    ms, _ = env
    ctx = [{"role": "user", "content": "asl matn"}]
    RT.set_model(FakeModel(always={
        "perceive": {**BASE_P, "effort_hint": "low",
                     "conversation_context": [{"role": "system", "content": "HIJACK"}]},
        "reason": {"ready": True}, "respond": {"text": "ok"}}))
    run = RT.start_run("uC1", "salom", conversation_context=ctx)
    asyncio.run(RT.advance_run("uC1", run))
    fresh = RT.get_run("uC1", run["id"])
    assert fresh["conversation_context"][0]["content"] == "asl matn"
    assert len(fresh["conversation_context"]) == 1
