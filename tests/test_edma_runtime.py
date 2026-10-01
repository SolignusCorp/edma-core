"""EDMA Runtime tests — trajectories, waits, resume, non-progress, scrubbing.

Runs the real runtime loop against a FAKE CognitiveModel (scripted semantic
outputs) and an in-memory user_state store — no network, no Supabase.
"""
import sys, os, asyncio, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from edma_core import dop
from edma_core.components import ComponentResult
from edma_core import runtime as RT


# ---------------------------------------------------------------- harness
class MemStore:
    """DocStore port'ning in-memory nusxasi (test uchun)."""
    def __init__(self):
        self.docs = {}
    def get(self, uid, key): return self.docs.get((uid, key))
    def put(self, uid, key, data):
        self.docs[(uid, key)] = json.loads(json.dumps(data, default=str)); return True
    def user_doc_get(self, uid, key): return self.get(uid, key)
    def user_doc_put(self, uid, key, data): return self.put(uid, key, data)


class FakeModel(RT.CognitiveModel):
    """Scripted semantic outputs per mode; counts calls; never touches network."""
    def __init__(self, script=None, always=None):
        super().__init__(model="fake")
        self.script = script or {}
        self.always = always or {}
        self.modes_called = []

    async def call(self, mode, payload, max_tokens=520, temperature=0.2):
        self.modes_called.append(mode)
        if mode in self.always:
            return ComponentResult(mode=mode, ok=True, data=dict(self.always[mode]),
                                   source="model", provider="fake", simulated=False,
                                   usage={"prompt_tokens": 10, "completion_tokens": 10})
        if mode in self.script:
            item = self.script[mode]
            data = item.pop(0) if isinstance(item, list) else item
            if data is None:  # simulate model failure -> heuristic fallback
                return ComponentResult(mode=mode, ok=False, error="scripted_failure")
            return ComponentResult(mode=mode, ok=True, data=dict(data), source="model",
                                   provider="fake", simulated=False,
                                   usage={"prompt_tokens": 10, "completion_tokens": 10})
        return ComponentResult(mode=mode, ok=False, error="no_script")


@pytest.fixture()
def env(monkeypatch):
    ms = MemStore()
    monkeypatch.setattr(RT, "_store", ms)      # DocStore port (test seam)
    monkeypatch.setattr(RT, "_wallet", None)   # Budget port yo'q
    monkeypatch.setattr(RT, "_MODEL", None)
    # MEMORY-AGNOSTIC: memory shim YO'Q — runtime memory bilan ishlamaydi
    monkeypatch.setattr("edma_core.verification._store", None)
    import edma_core.ports as _ports
    _ports.reset()
    return ms, monkeypatch


def run_edma(env, uid, message, script=None, always=None):
    ms, mp = env
    RT.set_model(FakeModel(script=script or {}, always=always or {}))
    run = RT.start_run(uid, message)
    view = asyncio.get_event_loop().run_until_complete(RT.advance_run(uid, run)) \
        if False else asyncio.run(RT.advance_run(uid, run))
    return view, RT.get_run(uid, run["id"])


MAXH = 24  # (runtime MAX_STEPS_HARD bilan bir xil tartib — pastda import qilinadi)
from edma_core.runtime import MAX_STEPS_HARD as _MSH
MAXH = _MSH

# ---------------------------------------------------------------- trajectories
def test_simple_qa_trajectory(env):
    view, run = run_edma(env, "u1", "Kvadrat ildiz 16 nechchi?",
        always={"perceive": {"intent": "question", "requires_clarification": False,
                             "action_candidate": "none"},
                "energy": {"complexity": "low"},
                "reason": {"ready": True, "response_draft": "4"},
                "respond": {"text": "Javob: 4"}})
    assert view["status"] == "complete" and view["state"] == dop.END
    assert view["dop_path"] == ["START", "PERCEIVING", "ENERGY_EVALUATION",
                                "REASONING", "RESPONSE"]
    assert view["response"] == "Javob: 4"


def test_clarification_wait_and_resume(env):
    ms, mp = env
    RT.set_model(FakeModel(always={
        "perceive": {"intent": "general", "requires_clarification": True,
                     "clarification_question": "Qaysi shahar haqida?",                     "action_candidate": "none"},
        "energy": {"complexity": "low"},
    }))
    run = RT.start_run("u1", "ayt")
    view = asyncio.run(RT.advance_run("u1", run))
    assert view["status"] == "waiting_clarification" and view["state"] == dop.CLARIFICATION_REQUIRED
    assert view["question"] == "Qaysi shahar haqida?"
    # no REASONING happened yet — waiting is not a transition
    assert "REASONING" not in view["dop_path"]
    # resume with an answer
    view2 = asyncio.run(RT.answer_run("u1", run["id"], text="Toshkent haqida"))
    assert view2["status"] == "complete" and view2["state"] == dop.END
    assert view2["dop_path"][-1] == dop.RESPONSE  # oxirgi bajarilgan holat
    assert "CLARIFICATION_REQUIRED" in view2["dop_path"]


def _script_action_flow(env, search_rows=True):
    ms, mp = env
    from edma_core import actions as A
    class FakeWallet:
        def cost_of(self, spec): return 0.002
        def cost_of(self, spec): return 0.002
        def try_spend(self, *a, **k): return True
        def charge(self, *a, **k): return {}
        def balance(self, uid): return 5.0
    class FakeSE:
        SEARCH_QUERY_COST = 0.002
        @staticmethod
        def search(q, n):
            if not search_rows:
                return {"results": [], "provider": "duckduckgo"}
            return {"results": [{"title": "Search result",
                                 "url": "https://x.io/a",
                                 "content": "web search findings about the queried topic"}],
                    "provider": "tavily"}
        @staticmethod
        def citations_of(rows): return [{"title": "t", "url": "https://x.io/a"}]
    from tests.helpers import reg_fake_search
    reg_fake_search(mp, A, FakeSE)
    mp.setattr("edma_core.runtime._wallet", FakeWallet())


def test_action_flow_with_verification_pass(env):
    _script_action_flow(env, search_rows=True)
    view, run = run_edma(env, "u1", "OpenAI haqida topib ber",
        always={"perceive": {"intent": "search", "requires_clarification": False,
                             "action_candidate": "web.search"},
                "energy": {"complexity": "medium"},
                "reason": {"propose_action": {"type": "web.search",
                                              "args": {"query": "OpenAI", "max_results": 3}}},
                "respond": {"text": "Topildi: https://x.io/a"}})
    assert view["dop_path"] == ["START", "PERCEIVING", "ENERGY_EVALUATION", "REASONING",
                                "ACTION_DECISION", "ACTION_AUTHORIZATION", "ACTION",
                                "VERIFICATION", "RESPONSE"]
    assert view["verification"]["verdict"] == "PASS"
    assert view["status"] == "complete"


def test_action_verification_fail_routes_to_reasoning_then_escalates(env):
    _script_action_flow(env, search_rows=False)  # provider returns no rows -> FAIL
    view, run = run_edma(env, "u1", "nomi yo'q narsani izla",
        always={"perceive": {"intent": "search", "requires_clarification": False,
                             "action_candidate": "web.search"},
                "energy": {"complexity": "medium"},
                "reason": {"propose_action": {"type": "web.search", "args": {"query": "xyz"}}},
                "respond": {"text": "Topilmadi — izoh bilan."}})
    path = view["dop_path"]
    assert path.count("VERIFICATION") >= 2          # action failed -> retried -> failed again
    assert path.count("REASONING") >= 2
    assert "ESCALATION_CHECK" in path and "ESCALATION" in path
    assert path[-1] == dop.RESPONSE and view["state"] == dop.END
    assert view["escalation"] and "non-progress" in view["escalation"]["reason"]
    assert view["verification"]["verdict"] == "FAIL"


def test_human_approval_pause_and_grant(env):
    ms, mp = env
    from edma_core import actions as A
    class FakeWallet:
        def cost_of(self, spec): return 0.002
        def balance(self, uid): return 10.0
        def charge(self, *a, **k): return {}
    mp.setattr("edma_core.runtime._wallet", FakeWallet())
    calls = {"n": 0}
    def fake_create(uid, args, ctx):
        calls["n"] += 1
        return {"ok": True, "dataset_id": "ds_x", "samples": 10,
                "evidence": {"dataset_id": "ds_x", "samples": 10, "topic": args.get("topic")}}
    class _GetDS:
        def get_dataset(self, ds_id, uid):
            return {"id": ds_id, "examples": [{"messages": []}] * 10}
    from tests.helpers import fake_dataset_spec
    spec = fake_dataset_spec(fake_create)
    mp.setitem(A.ACTION_REGISTRY, "dataset.create", spec)
    mp.setattr("edma_core.verification._store", _GetDS())
    view, run = run_edma(env, "u1", "dataset kerak",
        always={"perceive": {"intent": "dataset", "requires_clarification": False,
                             "action_candidate": "dataset.create"},
                "energy": {"complexity": "high"},
                "reason": {"propose_action": {"type": "dataset.create",
                                              "args": {"topic": "test", "size": 10}}}})
    assert view["status"] == "waiting_approval"
    assert view["state"] == dop.ACTION_AUTHORIZATION
    assert view["approval_request"]["action"] == "dataset.create"
    assert calls["n"] == 0  # nothing executed before approval
    # approve
    view2 = asyncio.run(RT.answer_run("u1", run["id"], approve=True))
    assert view2["status"] == "complete" and calls["n"] == 1
    assert "ACTION" in view2["dop_path"] and "VERIFICATION" in view2["dop_path"]
    assert view2["verification"]["verdict"] == "PASS"


def test_human_approval_denied_routes_escalation_check(env):
    ms, mp = env
    from edma_core import actions as A
    class FakeWallet:
        def cost_of(self, spec): return 0.002
        def balance(self, uid): return 10.0
    mp.setattr("edma_core.runtime._wallet", FakeWallet())
    from tests.helpers import fake_dataset_spec
    mp.setitem(A.ACTION_REGISTRY, "dataset.create", fake_dataset_spec(
        lambda uid, args, ctx: {"ok": True, "dataset_id": "ds_x", "samples": 10,
                                "evidence": {"dataset_id": "ds_x", "samples": 10}}))
    RT.set_model(FakeModel(always={
        "perceive": {"intent": "dataset", "requires_clarification": False,
                     "action_candidate": "dataset.create"},
        "energy": {"complexity": "high"},
        "reason": {"propose_action": {"type": "dataset.create", "args": {"topic": "t", "size": 10}}},
    }))
    run = RT.start_run("u1", "dataset")
    v1 = asyncio.run(RT.advance_run("u1", run))
    assert v1["status"] == "waiting_approval"
    v2 = asyncio.run(RT.answer_run("u1", run["id"], approve=False))
    assert "ESCALATION_CHECK" in v2["dop_path"]
    assert v2["state"] == dop.END  # denied once -> alternative reasoning -> respond/escalate


def test_model_cannot_authorize_or_skip_verification(env):
    """Task §10: model emits authorized/verified — runtime ignores them."""
    _script_action_flow(env, search_rows=False)
    view, run = run_edma(env, "u1", "test",
        always={"perceive": {"intent": "search", "requires_clarification": False,
                             "action_candidate": "web.search"},
                "energy": {"complexity": "low"},
                "reason": {"propose_action": {"type": "web.search", "args": {"query": "q"}},
                           "authorized": True, "verified": True, "state": "RESPONSE"},
                "respond": {"text": "called"}})
    tr = run["trace"]
    scrubbed = [t for t in tr if t.get("component", {}).get("ignored_model_fields")]
    assert scrubbed, "model control fields must be captured"
    assert scrubbed[0]["component"]["ignored_model_fields"].get("verified") is True
    # despite verified=true, real verification ran and FAILED (no rows)
    assert view["verification"]["verdict"] == "FAIL"


def test_non_progress_escalation(env):
    """Task §23: repeated identical reasoning -> ESCALATION_CHECK via semantics."""
    ms, mp = env
    always = {"perceive": {"intent": "x", "requires_clarification": False,
                           "action_candidate": "none"},
              "energy": {"complexity": "low"}}
    RT.set_model(FakeModel(always={
        **always,
        # identical FAIL-forever action proposal each time (same fingerprint)
        "reason": {"propose_action": {"type": "web.search", "args": {"query": "zzz"}}},
    }))
    from edma_core import actions as A
    class FakeWallet:
        def cost_of(self, spec): return 0.002
        def balance(self, uid): return 10.0
        def try_spend(self, *a, **k): return True
    class FakeSE:
        SEARCH_QUERY_COST = 0.002
        @staticmethod
        def search(q, n): return {"results": [], "provider": "tavily"}
        @staticmethod
        def citations_of(rows): return []
    from tests.helpers import reg_fake_search
    reg_fake_search(mp, A, FakeSE)
    fw = FakeWallet()
    RT._wallet = fw
    run = RT.start_run("u1", "izla zzz")
    view = asyncio.run(RT.advance_run("u1", run, max_steps=20))
    path = view["dop_path"]
    assert path.count("ESCALATION_CHECK") >= 1
    assert path.count("ESCALATION") >= 1
    assert path[-1] == dop.RESPONSE and view["state"] == dop.END
    # non-progress must be recorded somewhere in the trace
    assert any(t.get("component", {}).get("non_progress") for t in run["trace"])


def test_budget_step_cap_is_secondary_safety_net(env):
    """max_steps to'siqI: cheksiz FAIL action zanjiri ham step-cap bilan tugaydi."""
    _script_action_flow(env, search_rows=False)  # har search → FAIL
    always = {"perceive": {"intent": "x", "requires_clarification": False,
                           "action_candidate": "web.search"},
              "energy": {"complexity": "low"},
              "reason": {"propose_action": {"type": "web.search", "args": {"query": "q"}}}}
    RT.set_model(FakeModel(always=always))
    run = RT.start_run("u1", "uzun zanjir")
    view = asyncio.run(RT.advance_run("u1", run, max_steps=8))
    # qisqa slice → PENDING (worker davom etadi); cheksiz loop ham shu yerda to'xtaydi
    assert view["status"] == "slice_pending" and view["lifecycle"] == "pending"
    assert len(view["dop_path"]) <= 9
    # to'liq budjet (MAX_STEPS_HARD) → terminal (escalated yoki failed)
    view2 = asyncio.run(RT.advance_run("u1", RT.get_run("u1", run["id"]), max_steps=MAXH))
    assert view2["lifecycle"] in ("failed", "escalated")


def test_component_crash_becomes_error_facts_not_silence(env):
    ms, mp = env
    class Boom(RT.CognitiveModel):
        async def call(self, mode, payload, max_tokens=520, temperature=0.2):
            raise RuntimeError("boom")
    RT.set_model(Boom(model="boom"))
    run = RT.start_run("u1", "salom")
    view = asyncio.run(RT.advance_run("u1", run))
    assert "ESCALATION_CHECK" in view["dop_path"] or view["status"] == "complete"
    tr = run["trace"]
    assert any("component_exception" in json.dumps(t.get("component", {})) for t in tr)


def test_isolation_other_org_cannot_load_run(env):
    view, run = run_edma(env, "u1", "salom",
        always={"perceive": {"intent": "x", "requires_clarification": False,
                             "action_candidate": "none"},
                "energy": {"complexity": "low"},
                "reason": {"ready": True, "response_draft": "r"},
                "respond": {"text": "r"}})
    assert RT.get_run("u2", run["id"]) is None  # org isolation


def test_delete_run(env):
    view, run = run_edma(env, "u1", "salom",
        always={"perceive": {"intent": "x", "requires_clarification": False,
                             "action_candidate": "none"},
                "energy": {"complexity": "low"},
                "reason": {"ready": True, "response_draft": "r"},
                "respond": {"text": "r"}})
    assert RT.delete_run("u1", run["id"])
    assert RT.get_run("u1", run["id"]).get("deleted") is True


