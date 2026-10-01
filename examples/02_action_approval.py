"""EDMA-CORE action approval — default-deny, human gate, evidence verification.

Demonstrates the core contract:
  1. an UNREGISTERED action is default-denied (the model cannot invent powers);
  2. a registered high-risk action parks the run at ACTION_AUTHORIZATION
     (pending_human) — the CALLER approves, never the model;
  3. execution evidence decides the verdict — a claimed success is not a success.
"""
import asyncio

from edma_core import quickstart, runtime as RT
from edma_core import actions as A
from edma_core import verification as V
from edma_core.contrib import make_web_search_spec


def my_search(query: str, max_results: int) -> dict:
    """YOUR search backend (API, database, whatever). Bounded, honest."""
    return {"provider": "my-search", "results": [
        {"title": "Example", "url": "https://example.com/a",
         "content": f"findings about {query}"}]}


async def main() -> None:
    quickstart()
    A.register(make_web_search_spec(my_search))  # capability = explicit registration

    RT.set_default_model("default")

    # --- scripted model for THIS demo (in production: your real provider) ---
    from edma_core.components import CognitiveModel, ComponentResult

    class FakeModel(CognitiveModel):
        """Demo model: returns the same structured script (no network)."""
        def __init__(self, always):
            super().__init__(model="default")
            self.always = always

        async def call(self, mode, payload, max_tokens=520, temperature=0.2):
            return ComponentResult(mode=mode, ok=True, data=dict(self.always.get(mode, {})),
                                   source="model", provider="demo", simulated=False,
                                   usage={"prompt_tokens": 10, "completion_tokens": 10,
                                          "total_tokens": 20})

    RT.set_model(FakeModel(always={
        "perceive": {"intent": "research", "requires_clarification": False,
                     "action_candidate": "web.search", "effort_hint": "high"},
        "energy": {"complexity": "high"},
        "reason": {"ready": False, "response_draft": "",
                   "propose_action": {"type": "web.search",
                                      "args": {"query": "edma evidence runtime"}},
                   "action_needed": True},
        "respond": {"text": "Here is what the search found (verified)."}}))

    run = RT.start_run("org-demo", "search for evidence about edma")
    view = await RT.advance_run("org-demo", run)

    # low-risk action: auto-granted, executed, evidence-verified
    print("state        :", view["state"])
    print("dop_path     :", " -> ".join(view["dop_path"]))
    print("verification :", view.get("verification"))

    # an UNREGISTERED action type is default-denied — model is powerless here:
    res = A.execute("shell.exec", {"cmd": "ls"}, {"uid": "org-demo"})
    print("shell.exec   :", res.error)  # -> default_deny: unregistered action
    verdict = V.verify_action("shell.exec", res.to_dict(), "org-demo")
    print("verdict      :", verdict.result, "-", verdict.reason)


if __name__ == "__main__":
    asyncio.run(main())
