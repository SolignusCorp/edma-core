"""EDMA DOP Engine tests — frozen 13-state graph, policy authority, model cannot route.

MEMORY-AGNOSTIC spec (hard decoupling): exactly 13 states, no MEMORY_READ /
MEMORY_MUTATE anywhere; the transition table below IS the spec matrix.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from edma_core import dop


SPEC_TRANSITIONS = {
    "START": {"PERCEIVING"},
    "PERCEIVING": {"ENERGY_EVALUATION", "REASONING", "CLARIFICATION_REQUIRED"},
    "ENERGY_EVALUATION": {"REASONING"},
    "REASONING": {"VERIFICATION", "ACTION_DECISION", "ESCALATION_CHECK", "RESPONSE"},
    "VERIFICATION": {"REASONING", "ACTION_DECISION", "ESCALATION_CHECK", "RESPONSE"},
    "ACTION_DECISION": {"ACTION_AUTHORIZATION"},
    "ACTION_AUTHORIZATION": {"ACTION", "ESCALATION_CHECK", "REASONING"},
    "ACTION": {"VERIFICATION"},
    "ESCALATION_CHECK": {"ESCALATION", "REASONING", "RESPONSE"},
    "ESCALATION": {"RESPONSE"},
    "CLARIFICATION_REQUIRED": {"RESPONSE"},
    "RESPONSE": {"END"},
    "END": set(),
}


def test_frozen_state_set():
    expected = {"START", "PERCEIVING", "ENERGY_EVALUATION", "REASONING",
                "VERIFICATION", "ACTION_DECISION", "ACTION_AUTHORIZATION",
                "ACTION", "ESCALATION_CHECK", "ESCALATION", "CLARIFICATION_REQUIRED",
                "RESPONSE", "END"}
    assert dop.STATES == frozenset(expected)
    assert len(dop.STATES) == 13
    # memory states MUST NOT exist
    assert not any("MEMORY" in s for s in dop.STATES)
    assert not hasattr(dop, "MEMORY_READ") and not hasattr(dop, "MEMORY_MUTATE")


def test_transition_count_matches_spec():
    for state, targets in SPEC_TRANSITIONS.items():
        assert dop.legal_targets(state) == targets, state
    assert len(dop.LEGAL_TRANSITIONS) == 24


@pytest.mark.parametrize("cur,nxt", [
    (cur, nxt) for cur, targets in SPEC_TRANSITIONS.items() for nxt in sorted(targets)
])
def test_all_legal_transitions(cur, nxt):
    assert dop.is_legal(cur, nxt)


@pytest.mark.parametrize("cur,nxt", [
    ("PERCEIVING", "ACTION"),                 # forbidden: components bypass DOP
    ("REASONING", "ACTION"),                  # forbidden: must pass ACTION_DECISION
    ("REASONING", "ACTION_AUTHORIZATION"),    # forbidden: must pass ACTION_DECISION
    ("ACTION", "RESPONSE"),                   # ACTION -> RESPONSE without VERIFICATION
    ("ACTION", "END"),
    ("REASONING", "END"),                     # terminal shortcut forbidden
    ("VERIFICATION", "END"),
    ("PERCEIVING", "ACTION_AUTHORIZATION"),
    ("ENERGY_EVALUATION", "RESPONSE"),
    ("START", "RESPONSE"),
    ("ACTION_DECISION", "ACTION"),            # must pass through AUTHORIZATION
    ("ESCALATION", "END"),                    # must go through RESPONSE
    ("RESPONSE", "REASONING"),                # RESPONSE is terminal except END
    ("END", "PERCEIVING"),                    # END -> nothing
    ("CLARIFICATION_REQUIRED", "REASONING"),  # only -> RESPONSE
    ("REASONING", "REASONING"),               # no self-loops in frozen graph
    ("ACTION", "ACTION"),
])
def test_all_forbidden_transitions(cur, nxt):
    assert not dop.is_legal(cur, nxt)
    with pytest.raises(dop.TransitionError):
        dop.assert_legal(cur, nxt)


def test_unknown_states_rejected():
    with pytest.raises(dop.TransitionError):
        dop.assert_legal("THINKING", "ACTION")
    with pytest.raises(dop.TransitionError):
        dop.decide("NOT_A_STATE", dop.Facts())


def test_model_cannot_force_state():
    """Task §4/§10: model-emitted control fields are ignored by the policy."""
    f = dop.Facts()
    f.ignored_model_fields = {"next_state": "ACTION", "state": "ACTION",
                              "authorized": True, "verified": True}
    f.proposal = "respond"  # semantic proposal says respond…
    d = dop.decide(dop.REASONING, f)
    assert d.nxt == dop.RESPONSE  # …not ACTION


def test_policy_cannot_create_illegal_transition():
    """Even an injected policy is constrained by the frozen graph."""
    def rogue_policy(cur, f):
        return [(True, "ACTION", "rogue shortcut")]  # PERCEIVING -> ACTION is illegal
    with pytest.raises(dop.TransitionError):
        dop.decide(dop.PERCEIVING, dop.Facts(), policy=rogue_policy)


def test_default_policy_totality():
    """Every non-END state resolves under default policy with bare facts."""
    for s in dop.STATES:
        if s == dop.END:
            continue
        d = dop.decide(s, dop.Facts())
        assert dop.is_legal(s, d.nxt)


def test_decision_chain_shape_from_spec():
    f = dop.Facts()
    assert dop.decide(dop.START, f).nxt == dop.PERCEIVING
    f.clarification_required = True
    assert dop.decide(dop.PERCEIVING, f).nxt == dop.CLARIFICATION_REQUIRED
    f2 = dop.Facts()
    assert dop.decide(dop.PERCEIVING, f2).nxt == dop.ENERGY_EVALUATION
    assert dop.decide(dop.ENERGY_EVALUATION, f2).nxt == dop.REASONING
    f3 = dop.Facts(proposal="action", action_proposed={"type": "web.search", "args": {}})
    assert dop.decide(dop.REASONING, f3).nxt == dop.ACTION_DECISION
    assert dop.decide(dop.ACTION_DECISION, f3).nxt == dop.ACTION_AUTHORIZATION
    f3.authorization = {"status": "granted"}
    assert dop.decide(dop.ACTION_AUTHORIZATION, f3).nxt == dop.ACTION
    f3.verification = {"verdict": "PASS", "target": "action", "mandatory": True}
    f3.action_executed = True
    assert dop.decide(dop.ACTION, f3).nxt == dop.VERIFICATION
    assert dop.decide(dop.VERIFICATION, f3).nxt == dop.RESPONSE
    assert dop.decide(dop.RESPONSE, f3).nxt == dop.END


def test_skipped_verification_never_routes_as_pass_when_mandatory():
    f = dop.Facts(verification={"verdict": "SKIPPED", "mandatory": True, "target": "action"})
    assert dop.decide(dop.VERIFICATION, f).nxt == dop.ESCALATION_CHECK
    f2 = dop.Facts(verification={"verdict": "SKIPPED", "mandatory": False, "target": "action"})
    assert dop.decide(dop.VERIFICATION, f2).nxt == dop.REASONING


def test_action_verdicts_route_correctly():
    base = {"target": "action", "mandatory": True}
    f = dop.Facts(verification={**base, "verdict": "FAIL"}, action_executed=True)
    assert dop.decide(dop.VERIFICATION, f).nxt == dop.REASONING
    f = dop.Facts(verification={**base, "verdict": "UNCERTAIN"}, action_executed=True)
    assert dop.decide(dop.VERIFICATION, f).nxt == dop.REASONING
    f = dop.Facts(verification={**base, "verdict": "PASS"}, action_executed=True)
    assert dop.decide(dop.VERIFICATION, f).nxt == dop.RESPONSE


def test_facts_carry_no_memory_fields():
    """MEMORY-AGNOSTIC: Facts dataclass hech qanday memory maydoni saqlamaydi."""
    import dataclasses
    names = {f.name for f in dataclasses.fields(dop.Facts)}
    for bad in ("memory_relevant", "memory_status", "memory_provider",
                "mutation_pending", "mutation_verified"):
        assert bad not in names
