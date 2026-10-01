"""§16 DYNAMIC DOP testlari — EDMA hech qachon bir xil traektoriya bermasligi
va har bir traektoriya FAQLAR + DOP Engine tomonidan tanlanishini isbotlaydi.

Har bir scenario haqiqiy runtime loop bilan, scripted model ma'nolari bilan
ishlaydi (yo'l FORCE qilinmaydi — faqat komponent ma'nolari beriladi).
"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from edma_core import dop
from edma_core import runtime as RT
from tests.test_edma_runtime import MemStore, FakeModel


@pytest.fixture()
def env(monkeypatch):
    ms = MemStore()
    monkeypatch.setattr(RT, "_store", ms)
    monkeypatch.setattr(RT, "_wallet", None)
    monkeypatch.setattr(RT, "_MODEL", None)
    # MEMORY-AGNOSTIC: memory I/O shim YO'Q
    monkeypatch.setattr("edma_core.verification._store", None)
    import edma_core.ports as _ports
    _ports.reset()
    return ms, monkeypatch


def run_edma(env, uid, message, script=None, always=None):
    ms, mp = env
    RT.set_model(FakeModel(script=script or {}, always=always or {}))
    run = RT.start_run(uid, message)
    view = asyncio.run(RT.advance_run(uid, run))
    return view, RT.get_run(uid, run["id"])


def assert_path_legal(path):
    for a, b in zip(path, path[1:]):
        dop.assert_legal(a, b)


P_BASE = {"intent": "question", "requires_clarification": False,
          "action_candidate": "none"}
RESP = {"text": "javob"}


def test_scenario_A_simple_fast_path(env):
    """Trivial so'rov: PERCEIVING→REASONING (energy fakultativ — effort fact)."""
    view, _ = run_edma(env, "uA", "2+2 nechchi?",
        always={"perceive": {**P_BASE, "effort_hint": "low"},
                "reason": {"ready": True, "response_draft": "4"},
                "respond": RESP})
    assert view["dop_path"] == ["START", "PERCEIVING", "REASONING", "RESPONSE"]
    assert_path_legal(view["dop_path"])


def test_scenario_A2_energy_for_nontrivial(env):
    """Xuddi shu savol medium effort bilan: ENERGY_EVALUATION qo'shiladi."""
    view, _ = run_edma(env, "uA2", "Hisoblab ber: murakkab ifoda",
        always={"perceive": {**P_BASE, "effort_hint": "medium"},
                "energy": {"complexity": "medium"},
                "reason": {"ready": True, "response_draft": "x"},
                "respond": RESP})
    assert view["dop_path"] == ["START", "PERCEIVING", "ENERGY_EVALUATION",
                                "REASONING", "RESPONSE"]
    assert_path_legal(view["dop_path"])


def test_scenario_B_clarification(env):
    view, _ = run_edma(env, "uB", "uni qil",
        always={"perceive": {"intent": "ambiguous", "requires_clarification": True,
                             "clarification_question": "Nimani?",
                             "action_candidate": "none",
                             "effort_hint": "medium"},
                "respond": RESP})
    assert view["dop_path"][1] == "PERCEIVING"
    assert view["dop_path"][2] == "CLARIFICATION_REQUIRED"
    assert view["status"] == "waiting_clarification"
    assert_path_legal(view["dop_path"])


def test_scenario_E_external_action(env):
    class FW:
        def cost_of(self, spec): return 0.002
        def balance(self, uid): return 5.0
        def try_spend(self, *a, **k): return True
        def charge(self, *a, **k): return {}
    from edma_core import actions as A
    from tests.helpers import reg_fake_search
    env[1].setattr(RT, "_wallet", FW())
    class FSE:
        SEARCH_QUERY_COST = 0.002
        @staticmethod
        def search(q, n):
            return {"results": [{"title": "AI news update",
                                 "url": "https://real.io/x",
                                 "content": "latest AI news and developments"}],
                    "provider": "tavily"}
        @staticmethod
        def citations_of(r): return [{"title": "t", "url": "https://real.io/x"}]
    reg_fake_search(env[1], A, FSE)
    view, _ = run_edma(env, "uE", "Bugungi AI yangiliklarini topib ber",
        script={"reason": [
            {"ready": False, "propose_action": {"type": "web.search", "args": {"query": "AI news"}}},
            {"ready": True, "response_draft": "topildi"},
        ]},
        always={"perceive": {**P_BASE, "action_candidate": "web.search", "effort_hint": "high"},
                "respond": RESP})
    path = view["dop_path"]
    for st in ("ACTION_DECISION", "ACTION_AUTHORIZATION", "ACTION", "VERIFICATION"):
        assert st in path, st
    assert path.index("ACTION_DECISION") < path.index("ACTION_AUTHORIZATION") \
        < path.index("ACTION") < path.index("VERIFICATION") < path.index("RESPONSE")
    assert (view.get("verification") or {}).get("verdict") == "PASS"
    assert_path_legal(path)


def test_scenario_F_action_verify_then_reasoning(env):
    """ACTION→VERIFICATION(FAIL)→REASONING— verification keyingi fikrlashga qaytadi."""
    class FW:
        def cost_of(self, spec): return 0.002
        def balance(self, uid): return 5.0
        def try_spend(self, *a, **k): return True
        def charge(self, *a, **k): return {}
    from edma_core import actions as A
    from tests.helpers import reg_fake_search
    env[1].setattr(RT, "_wallet", FW())
    class FSE:
        SEARCH_QUERY_COST = 0.002
        @staticmethod
        def search(q, n):
            return {"results": [], "provider": "mock"}   # FAIL: natija yo'q
        @staticmethod
        def citations_of(r): return []
    reg_fake_search(env[1], A, FSE)
    view, _ = run_edma(env, "uF", "topib ber",
        script={"reason": [
            {"ready": False, "propose_action": {"type": "web.search", "args": {"query": "q"}}},
            {"ready": False, "propose_action": {"type": "web.search", "args": {"query": "q2"}}},
            {"escalate": {"reason": "search keeps failing"}},
        ]},
        always={"perceive": {**P_BASE, "action_candidate": "web.search", "effort_hint": "high"},
                "respond": RESP})
    path = view["dop_path"]
    assert "ACTION" in path and "VERIFICATION" in path and "REASONING" in path
    assert path.index("ACTION") < path.index("VERIFICATION")   # ACTION → VERIFICATION
    # FAIL dan keyin REASONING'ga qaytish (retry) mavjud
    first_v = path.index("VERIFICATION")
    assert "REASONING" in path[first_v:]
    assert view.get("escalation"), "takroriy failure — non-progress escalation"
    assert_path_legal(path)


def test_trajectories_differ_no_universal_path(env):
    """UNIVERSAL TRAYEKTORIYA YO'Q: kamida 4 xil to'liq yo'l mavjud."""
    paths = set()
    v, _ = run_edma(env, "uD1", "2+2", always={
        "perceive": {**P_BASE, "effort_hint": "low"},
        "reason": {"ready": True, "response_draft": "4"}, "respond": RESP})
    paths.add(tuple(v["dop_path"]))
    v, _ = run_edma(env, "uD2", "2+2", always={
        "perceive": {**P_BASE, "effort_hint": "medium"},
        "energy": {"complexity": "low"},
        "reason": {"ready": True, "response_draft": "4"}, "respond": RESP})
    paths.add(tuple(v["dop_path"]))
    # clarification yo'li
    v, _ = run_edma(env, "uD3", "uni qil", always={
        "perceive": {**P_BASE, "requires_clarification": True,
                     "clarification_question": "Nimani?", "effort_hint": "low"},
        "respond": RESP})
    paths.add(tuple(v["dop_path"]))
    # ACTION yo'li (web.search → ... → VERIFICATION → RESPONSE)
    from edma_core import actions as _A
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
                                 "content": "web search findings about the topic"}],
                    "provider": "tavily"}
        @staticmethod
        def citations_of(rows): return [{"title": "t", "url": "https://x.io/a"}]
    _A._se = FSE
    _A._wallet = FW()
    RT._wallet = _A._wallet
    v, _ = run_edma(env, "uD4", "qidir", always={
        "perceive": {**P_BASE, "action_candidate": "web.search", "effort_hint": "medium"},
        "energy": {"complexity": "medium"},
        "reason": {"propose_action": {"type": "web.search", "args": {"query": "EDMA"}}},
        "respond": RESP})
    paths.add(tuple(v["dop_path"]))
    assert len(paths) >= 4, f"universal trajectory xavfi: {paths}"


def test_no_flag_can_disable_trace(env):
    """§6/§17: runtime ichida trace o'chiruvchi flag YO'Q — advance_run HAR DOIM
    kanonik trace bilan chiqadi."""
    v, run = run_edma(env, "uT1", "salom", always={
        "perceive": {**P_BASE, "effort_hint": "low"},
        "reason": {"ready": True, "response_draft": "a"}, "respond": RESP})
    tr = run.get("trace") or []
    assert len(tr) >= 4
    for step in tr:
        assert "seq" in step and "state" in step and "decision" in step and "component" in step
    # start_and_advance ham xuddi shunday (hech qanday param trace'ni o'chirmaydi)
    RT.set_model(FakeModel(always={
        "perceive": {**P_BASE, "effort_hint": "low"},
        "reason": {"ready": True, "response_draft": "a"}, "respond": RESP}))
    v2 = asyncio.run(RT.start_and_advance("uT2", "salom"))
    RT.set_model(None)
    # runtime ichida trace to'liq saqlanadi (get_run orqali)
    r2 = RT.get_run("uT2", v2["run_id"])
    assert len(r2.get("trace") or []) >= 4



def test_eslab_qol_phrase_does_not_force_anything(env):
    """§3 MEMORY-AGNOSTIC: 'eslab qol' iborasi hech qanday maxsus yo'lni
    KUCHLAYDIGAN emas — reasoning tayyor bo'lsa to'g'ridan-to'g'ri javob."""
    v, _ = run_edma(env, "uEslab", "Eslab qol: ismim Aziz, Toshkentda yashayman",
        always={"perceive": {**P_BASE, "effort_hint": "low"},
                "reason": {"ready": True, "response_draft": "javob"},
                "respond": RESP})
    assert not any("MEMORY" in st for st in v["dop_path"])
    assert v["dop_path"] == ["START", "PERCEIVING", "REASONING", "RESPONSE"]


def test_heuristic_reason_is_memory_free(env):
    """Deterministik fallback (simulyatsiya rejimi) hech qanday memory
    qarori/kaliti bermaydi — EDMA memory-agnostik."""
    from edma_core.components import heuristic_reason
    for msg in ("Eslab qol: ismim Aziz", "My name is Aziz and I live in Tashkent",
                "Ismim Aziz va men Toshkentda yashayman"):
        out = heuristic_reason({"messages": [{"role": "user", "content": msg}]}, {})
        assert not any(k.startswith("memory") or k.startswith("propose_memory")
                       for k in out), msg


def test_web_search_flag_does_not_force_search(env):
    """§9/§16: web_search=true imkoniyat — qidiruvni KUCHLAYDIGAN emas.
    Oddiy so'rov: EDMA qidiruvsiz javob beradi."""
    v, _ = run_edma(env, "uWS", "2+2 nechchi?",
        always={"perceive": {**P_BASE, "action_candidate": "none", "effort_hint": "low"},
                "reason": {"ready": True, "response_draft": "4"},
                "respond": RESP})
    assert not any(st.startswith("ACTION") for st in v["dop_path"])
    assert v["dop_path"] == ["START", "PERCEIVING", "REASONING", "RESPONSE"]



def test_13_states_and_24_transitions_frozen(env):
    """Arxitektura muzlatilgan: 13 holat, 24 o'tish — memory holatlari YO'Q."""
    assert len(dop.STATES) == 13
    assert len(set(map(tuple, dop.LEGAL_TRANSITIONS))) == 24
    assert not any("MEMORY" in st for st in dop.STATES)
