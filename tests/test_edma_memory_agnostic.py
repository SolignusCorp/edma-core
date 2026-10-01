"""§13 MEMORY-AGNOSTIC acceptance — EDMA HARD DECOUPLING (2026-09-27 directive).

EDMA memory-agnostik orkestratsiya: memory'o'qilmaydi/yozilmaydi/boshqarilmaydi/
BILINMAYDI. Bu fayl direktiv §13 checklist'ini PIN qiladi:
  1) DOP aynan 13 holat; MEMORY_READ/MEMORY_MUTATE YO'Q
  2) memory action'lari / API'lari / managed store YO'Q
  3) conversation context modelga input sifatida YETADI
  4) EDMA context'ni hech qachon MUTATSIYA QILMAYDI (model ham)
  5) ACTION → VERIFICATION MAJBURIY (ACTION→RESPONSE taqiqlangan)
  6) DOP Engine yagona tranzitsiya avtoriteti
  7) illegal tranzitsiya RAD ETILADI
  8) trace'da memory maydonlari YO'Q
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
from tests.test_edma_runtime import env, FakeModel, run_edma  # noqa: F401


# ─────────────────────────── 1) 13-state DOP ───────────────────────────
def test_dop_has_exactly_13_states_no_memory_states():
    assert len(dop.STATES) == 13
    assert dop.STATES == frozenset({
        "START", "PERCEIVING", "ENERGY_EVALUATION", "REASONING", "VERIFICATION",
        "ACTION_DECISION", "ACTION_AUTHORIZATION", "ACTION", "ESCALATION_CHECK",
        "ESCALATION", "CLARIFICATION_REQUIRED", "RESPONSE", "END"})
    assert "MEMORY_READ" not in dop.STATES and "MEMORY_MUTATE" not in dop.STATES
    assert not hasattr(dop, "MEMORY_READ") and not hasattr(dop, "MEMORY_MUTATE")


# ──────────────────── 2) no memory actions / API / store ────────────────────
def test_action_registry_is_memory_free():
    # CORE: default registry faqat web.fetch; capability'lar plugin:
    assert set(A.ACTION_REGISTRY) == {"web.fetch"}
    assert "memory.recall" not in A.ACTION_REGISTRY


def test_core_sources_are_memory_free():
    """Core paketi (edma_core) XOTIRA TIZIMSIZ (memory-agnostic mandat)."""
    import glob
    assert not os.path.exists("src/edma_core/memory_io.py")
    for f in sorted(glob.glob("src/edma_core/*.py")):
        src = open(f, encoding="utf-8").read()
        for token in ("memory_io", "MEMORY_READ", "MEMORY_MUTATE",
                      "propose_memory_mutate", "memory_scope", "memory_relevant",
                      "personal_memories", "memory_entries"):
            assert token not in src, f"{f}: {token}"


# ──────────────────── 3) context reaches the model ────────────────────
def test_conversation_context_reaches_model_as_input(env):
    ms, mp = env
    captured = {}

    class Spy(FakeModel):
        async def call(self, mode, payload, max_tokens=520, temperature=0.2):
            if mode == "perceive":
                captured["blob"] = json.dumps(payload["messages"])
            return await super().call(mode, payload, max_tokens=max_tokens, temperature=temperature)

    RT.set_model(Spy(always={
        "perceive": {"intent": "q", "requires_clarification": False,
                     "action_candidate": "none"},
        "energy": {"complexity": "low"},
        "reason": {"ready": True, "response_draft": "ok"},
        "respond": {"text": "javob"}}))
    ctx = [{"role": "user", "content": "Mening loyiham EDMA deyiladi"},
           {"role": "assistant", "content": "Yaxshi, esdalik sifatida emas — shunchaki kontekst."}]
    run = RT.start_run("u1", "Loyihamiz nima deyiladi?", conversation_context=ctx)
    view = asyncio.run(RT.advance_run("u1", run))
    assert view["status"] == "complete"
    # application-context modelga YETDI (input sifatida)
    assert "EDMA deyiladi" in captured["blob"]


# ──────────────── 4) EDMA never mutates the context ────────────────
def test_edma_never_mutates_conversation_context(env):
    ms, mp = env
    ctx = [{"role": "user", "content": "aslini"}]
    RT.set_model(FakeModel(script={}, always={
        "perceive": {**{k: v for k, v in (
            ("intent", "task"), ("requires_clarification", False),
            ("effort_hint", "low"), ("action_candidate", "none"))},
            # model context'ni o'zgartirishga URINADI:
            "conversation_context": [{"role": "user", "content": "HACK"}]},
        "reason": {"ready": True}, "respond": {"text": "ok"}}))
    run = RT.start_run("u1", "salom", conversation_context=ctx)
    asyncio.run(RT.advance_run("u1", run))
    fresh = RT.get_run("u1", run["id"])
    assert fresh["conversation_context"][0]["content"] == "aslini"  # model o'zgartira olmadi
    assert len(fresh["conversation_context"]) == 1


# ──────────────── 5) ACTION → VERIFICATION is mandatory ────────────────
def test_action_must_be_verified_no_shortcut_to_response():
    assert not dop.is_legal("ACTION", "RESPONSE")
    assert not dop.is_legal("ACTION", "END")
    assert dop.legal_targets("ACTION") == {"VERIFICATION"}


# ──────────────── 6) DOP sole transition authority ────────────────
def test_dop_is_sole_authority_model_cannot_route():
    f = dop.Facts()
    f.ignored_model_fields = {"next_state": "ACTION", "authorized": True, "verified": True}
    f.proposal = "respond"
    assert dop.decide(dop.REASONING, f).nxt == dop.RESPONSE
    def rogue_policy(cur, f):
        return [(True, "ACTION", "rogue")]
    with pytest.raises(dop.TransitionError):
        dop.decide(dop.PERCEIVING, dop.Facts(), policy=rogue_policy)


# ──────────────── 7) illegal transitions are rejected ────────────────
def test_illegal_transitions_rejected():
    for cur, nxt in (("PERCEIVING", "ACTION"), ("REASONING", "ACTION"),
                     ("ACTION", "RESPONSE"), ("REASONING", "END"),
                     ("VERIFICATION", "END")):
        with pytest.raises(dop.TransitionError):
            dop.assert_legal(cur, nxt)


# ──────────────── 8) trace carries no memory fields ────────────────
def test_trace_is_memory_free_full_run(env):
    ms, mp = env
    from edma_core import actions as AA
    class FW:
        def cost_of(self, spec): return 0.002
        def balance(self, uid): return 5.0
        def try_spend(self, *a, **k): return True
        def charge(self, *a, **k): return {}
    class FSE:
        SEARCH_QUERY_COST = 0.002
        @staticmethod
        def search(q, n):
            return {"results": [{"title": "t", "url": "https://x.io/a",
                                 "content": "web search findings"}],
                    "provider": "tavily"}
        @staticmethod
        def citations_of(rows): return [{"title": "t", "url": "https://x.io/a"}]
    from tests.helpers import reg_fake_search
    reg_fake_search(mp, AA, FSE)
    mp.setattr("edma_core.runtime._wallet", FW())
    view, run = run_edma(env, "u1", "qidir",
        always={"perceive": {"intent": "search", "requires_clarification": False,
                             "action_candidate": "web.search"},
                "energy": {"complexity": "medium"},
                "reason": {"propose_action": {"type": "web.search",
                                              "args": {"query": "EDMA"}}},
                "respond": {"text": "topildi"}})
    assert view["status"] == "complete"
    blob = json.dumps(run.get("trace"))
    assert "MEMORY" not in blob
    assert "memory_status" not in blob and "memory_provider" not in blob
    # public view ham memory-free
    pub = json.dumps(view)
    for tok in ("memory_status", "memory_provider", "memory", "MEMORY"):
        assert tok not in pub, tok
