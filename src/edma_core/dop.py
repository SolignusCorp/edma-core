"""EDMA DOP Engine — the SOLE authority for DOP state transitions.

Purity contract (implementation task §9): no I/O, no model calls, no action
execution, no database access, no memory mutation. Deterministic and testable.

Authority boundary (§4):
  component execution result -> orchestration Facts -> dependency Facts
    -> decision policy -> FROZEN legal transition graph -> next state

Components and the model CANNOT route: `Facts` has no next-state field, and any
model-emitted field such as `next_state`/`state` lands in `Facts.ignored_model_fields`
where the policy never reads it. Alternative policies may be injected for tests,
but every policy target is validated against the frozen graph — an illegal
transition raises `TransitionError` regardless of the policy that proposed it.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

# ---------------------------------------------------------------- frozen states
START = "START"
PERCEIVING = "PERCEIVING"
ENERGY_EVALUATION = "ENERGY_EVALUATION"
REASONING = "REASONING"
VERIFICATION = "VERIFICATION"
ACTION_DECISION = "ACTION_DECISION"
ACTION_AUTHORIZATION = "ACTION_AUTHORIZATION"
ACTION = "ACTION"
ESCALATION_CHECK = "ESCALATION_CHECK"
ESCALATION = "ESCALATION"
CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
RESPONSE = "RESPONSE"
END = "END"

STATES = frozenset({
    START, PERCEIVING, ENERGY_EVALUATION, REASONING,
    VERIFICATION, ACTION_DECISION, ACTION_AUTHORIZATION, ACTION, ESCALATION_CHECK,
    ESCALATION, CLARIFICATION_REQUIRED, RESPONSE, END,
})

# ---------------------------------------------------------------- frozen transitions
_LEGAL: dict[str, set[str]] = {
    START: {PERCEIVING},
    PERCEIVING: {ENERGY_EVALUATION, REASONING, CLARIFICATION_REQUIRED},
    ENERGY_EVALUATION: {REASONING},
    REASONING: {VERIFICATION, ACTION_DECISION, ESCALATION_CHECK, RESPONSE},
    VERIFICATION: {REASONING, ACTION_DECISION, ESCALATION_CHECK, RESPONSE},
    ACTION_DECISION: {ACTION_AUTHORIZATION},
    ACTION_AUTHORIZATION: {ACTION, ESCALATION_CHECK, REASONING},
    ACTION: {VERIFICATION},
    ESCALATION_CHECK: {ESCALATION, REASONING, RESPONSE},
    ESCALATION: {RESPONSE},
    CLARIFICATION_REQUIRED: {RESPONSE},
    RESPONSE: {END},
    END: set(),
}

LEGAL_TRANSITIONS = frozenset((a, b) for a, targets in _LEGAL.items() for b in targets)


class TransitionError(Exception):
    """Raised when a proposed transition is not in the frozen graph."""


class PolicyError(Exception):
    """Raised when no policy rule matches a state (a policy bug, never a user error)."""


def is_legal(current: str, nxt: str) -> bool:
    return (current, nxt) in LEGAL_TRANSITIONS


def assert_legal(current: str, nxt: str) -> None:
    if current not in STATES or nxt not in STATES:
        raise TransitionError(f"unknown DOP state(s): {current!r} -> {nxt!r}")
    if not is_legal(current, nxt):
        raise TransitionError(f"illegal DOP transition: {current} -> {nxt}")


# ---------------------------------------------------------------- facts
@dataclass
class Facts:
    """Orchestration + dependency facts produced by executing the CURRENT state.

    The model's semantic decisions land here as plain fields (proposal, etc.).
    Anything the model emits to control the runtime (state names, authorized,
    verified, next_state ...) is collected by the runtime into
    `ignored_model_fields` — the policy NEVER reads that field.
    """
    # perception
    clarification_required: bool = False
    clarification_answered: bool = False
    intent: str = ""
    effort_hint: str = "medium"             # low | medium | high (perception fact)
    # energy
    energy_done: bool = False
    complexity: str = "low"                 # low | medium | high
    # reasoning semantic proposal: respond | action | verify | escalate
    proposal: str = "respond"
    ready_to_respond: bool = False
    response_text: Optional[str] = None     # pre-composed user-facing text (clarification/escalation/respond)
    # verification (dependency facts)
    verification: dict = field(default_factory=dict)   # {verdict, mandatory, target, evidence_summary}
    # action pipeline
    action_proposed: Optional[dict] = None             # {type, args} (semantic, unvalidated)
    action_invalid_reason: Optional[str] = None
    authorization: dict = field(default_factory=dict)  # {status: granted|denied|pending_human, reason}
    action_executed: bool = False
    action_result: dict = field(default_factory=dict)
    # FIX-2: op-graf fakti (deterministik hisob — runtime) — VERIFICATION policy
    # PASS'dan keyin davom etish kerakmi-ligini biladi (graffa pending op bor-yo'qligi)
    ops_pending: bool = False
    # escalation / control
    escalate_reason: Optional[str] = None
    non_progress: bool = False
    budget_exhausted: bool = False
    retries: int = 0
    error: Optional[str] = None
    # anything the model tried to use to control the runtime (never read by policy)
    ignored_model_fields: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- decision
@dataclass
class Decision:
    current: str
    nxt: str
    reason: str

    def to_dict(self) -> dict:
        return {"current": self.current, "next": self.nxt, "reason": self.reason}


def default_policy(current: str, f: Facts) -> list[tuple[bool, str, str]]:
    """Ordered rule table: (predicate, target_state, reason). First match wins.

    Data-driven, not an if/elif tree in the runtime. Targets are hard-validated
    against LEGAL_TRANSITIONS in decide() — the policy cannot invent transitions.
    """
    v = (f.verification or {}).get("verdict")
    v_mandatory = bool((f.verification or {}).get("mandatory"))
    v_target = (f.verification or {}).get("target")
    az = (f.authorization or {}).get("status")

    if current == START:
        return [(True, PERCEIVING, "run started — perception is the first cognitive step")]

    if current == PERCEIVING:
        return [
            (f.clarification_required and not f.clarification_answered,
             CLARIFICATION_REQUIRED, "request is ambiguous — clarification required"),
            (f.effort_hint == "low",
             REASONING, "perception: trivial effort — reasoning answers directly"),
            (True, ENERGY_EVALUATION, "perception complete — assess energy"),
        ]

    if current == ENERGY_EVALUATION:
        return [(True, REASONING, "energy assessed — reason")]

    if current == REASONING:
        return [
            (f.non_progress, ESCALATION_CHECK, "semantic non-progress detected in reasoning"),
            (f.budget_exhausted, ESCALATION_CHECK, "run budget exhausted"),
            (bool(f.error), ESCALATION_CHECK, f"component error: {f.error}"),
            (f.proposal == "escalate", ESCALATION_CHECK, f"reasoning proposed escalation: {f.escalate_reason}"),
            (f.proposal == "action" and bool(f.action_proposed),
             ACTION_DECISION, "reasoning proposed an action"),
            (f.proposal == "verify", VERIFICATION, "reasoning proposed verification of pending work"),
            (f.ready_to_respond or f.proposal == "respond",
             RESPONSE, "reasoning is ready to respond"),
            (True, ESCALATION_CHECK, "reasoning produced no actionable proposal"),
        ]

    if current == VERIFICATION:
        return [
            # FIX-2: op-grafda yana bajariladigan op bor — REASONING davom etadi
            # (eng oxirgi PASS op ham tarixda immutable qoladi)
            (v == "PASS" and v_target == "action" and f.action_executed and f.ops_pending,
             REASONING, "operation plan has pending operations — continue with next ready op"),
            (v == "PASS" and v_target == "action" and f.action_executed,
             RESPONSE, "action verified — respond with verified result"),
            (v == "PASS",
             RESPONSE, "verification passed"),
            (v == "FAIL",
             REASONING, "verification failed — retry with failure facts"),
            (v == "UNCERTAIN",
             REASONING, "verification uncertain — reconsider"),
            (v == "SKIPPED" and v_mandatory,
             ESCALATION_CHECK, "mandatory verification was skipped — must not be treated as PASS"),
            (v == "SKIPPED",
             REASONING, "verification skipped (not mandatory) — reconsider"),
            (True, ESCALATION_CHECK, "verification produced no verdict"),
        ]

    if current == ACTION_DECISION:
        # ACTION_DECISION has exactly one legal target: authorization first, always.
        return [(True, ACTION_AUTHORIZATION, "action requires authorization before execution")]

    if current == ACTION_AUTHORIZATION:
        return [
            (az == "granted", ACTION, "authorization granted"),
            (az == "denied", ESCALATION_CHECK, "authorization denied"),
            # pending_human never reaches decide(): the runtime waits before calling it.
            (True, ESCALATION_CHECK, "authorization did not conclude"),
        ]

    if current == ACTION:
        # ACTION -> RESPONSE is forbidden; verification is unconditional.
        return [(True, VERIFICATION, "action executed — verification is mandatory")]

    if current == ESCALATION_CHECK:
        return [
            (f.non_progress or f.budget_exhausted or bool(f.escalate_reason) and f.retries >= 2,
             ESCALATION, "hard escalation condition"),
            (bool(f.escalate_reason) and not f.non_progress and f.retries < 2,
             REASONING, "escalation reason may be recoverable — one more reasoning pass"),
            (az == "denied" and f.retries < 1,
             REASONING, "authorization denied — reconsider alternative approach"),
            (True, RESPONSE, "escalation check resolved — respond with current state"),
        ]

    if current == ESCALATION:
        return [(True, RESPONSE, "escalation message prepared")]

    if current == CLARIFICATION_REQUIRED:
        return [
            (f.clarification_answered and bool(f.response_text),
             RESPONSE, "clarification answered — deliver response"),
            # The runtime normally WAITS in this state (no transition). This
            # fallback only fires if decide() is invoked without an answer.
            (True, RESPONSE, "clarification unresolved at decide-time — respond with guidance"),
        ]

    if current == RESPONSE:
        return [(True, END, "response delivered")]

    if current == END:
        return []

    return []  # unknown state -> PolicyError below


def decide(current: str, facts: Facts, policy: Optional[Callable[[str, Facts], list[tuple[bool, str, str]]]] = None) -> Decision:
    """Sole transition authority. Returns the next legal state with its reason."""
    if current not in STATES:
        raise TransitionError(f"unknown DOP state: {current!r}")
    rules = (policy or default_policy)(current, facts)
    for ok, target, reason in rules:
        if ok:
            assert_legal(current, target)  # frozen graph is the last word
            return Decision(current=current, nxt=target, reason=reason)
    raise PolicyError(f"no policy rule matched for state {current}")


def legal_targets(current: str) -> set[str]:
    return set(_LEGAL.get(current, set()))


# ---------------------------------------------------------------- state contracts
# §COMPONENT ROLE INTEGRITY (audit §15): har state uchun aniq shartnoma.
# Bu FROZEN hujjat-data: hech qanday behavior yo'q; testlar uni _LEGAL bilan
# mosligiga PIN qiladi (drift bo'lsa test qizil bo'ladi).
# No hidden states — no implicit routing — no component-created states.
STATE_CONTRACTS: dict[str, dict] = {
    "START": {
        "allowed_input": "run creation (message + bounded application-provided conversation context)",
        "responsibility": "entry point — no cognition, no model calls",
        "output_facts": [],
        "legal_next": ["PERCEIVING"],
        "forbidden": ["model calls", "external actions", "route choice"],
    },
    "PERCEIVING": {
        "allowed_input": "current user request + CONVERSATION CONTEXT block (application input only)",
        "responsibility": "structure the input ONLY: intent, entities, language, ambiguity, "
                          "missing information, task type, action-candidate presence, clarification need, effort hint",
        "output_facts": ["clarification_required", "intent", "effort_hint", "language",
                         "action_candidate", "perception payload"],
        "legal_next": ["ENERGY_EVALUATION", "REASONING", "CLARIFICATION_REQUIRED"],
        "forbidden": ["solving the task", "final solution", "action execution", "authorization",
                      "verification PASS", "route choice (facts only — DOP decides)"],
    },
    "ENERGY_EVALUATION": {
        "allowed_input": "current request (assessment target)",
        "responsibility": "cognitive/execution cost estimate ONLY: complexity, reasoning load, "
                          "expected operation count, verification burden",
        "output_facts": ["complexity", "cognitive_load", "expected_operations", "verification_burden"],
        "legal_next": ["REASONING"],
        "forbidden": ["solving the task", "actions", "authorization", "routing", "final answer",
                      "substituting for reasoning"],
    },
    "REASONING": {
        "allowed_input": "request + bounded conversation context + perception facts + energy facts "
                         "+ execution/dependency facts + deterministic capabilities block",
        "responsibility": "primary cognitive decision layer: understand, goal, multi-step plan "
                          "(operation dependency graph proposal), action selection + arguments, "
                          "verification need, escalation need, response draft",
        "output_facts": ["proposal (respond|action|verify|escalate)", "ops plan", "propose_action",
                         "propose_verification", "escalate", "response_draft", "knowledge_assessment"],
        "legal_next": ["VERIFICATION", "ACTION_DECISION", "ESCALATION_CHECK", "RESPONSE"],
        "forbidden": ["next DOP state choice", "action execution", "authorization",
                      "declaring verification PASS", "direct user delivery"],
    },
    "VERIFICATION": {
        "allowed_input": "executed action result + evidence (+ immutable op ledger)",
        "responsibility": "independent control: EXPECTED vs ACTUAL vs EVIDENCE — verdict "
                          "PASS | FAIL | UNCERTAIN | SKIPPED (SKIPPED != PASS; no evidence -> no PASS)",
        "output_facts": ["verification verdict", "mandatory", "evidence_summary"],
        "legal_next": ["REASONING", "ACTION_DECISION", "ESCALATION_CHECK", "RESPONSE"],
        "forbidden": ["trusting model success/verified/confidence claims", "executing actions",
                      "auto-PASS on action completion"],
    },
    "ACTION_DECISION": {
        "allowed_input": "reasoning proposal (propose_action / ready op from the dependency graph)",
        "responsibility": "operational decision ONLY: registered action? required args present? "
                          "args schema valid? dependency readiness (op-graph ready set)",
        "output_facts": ["validated_action", "action_invalid_reason", "action_decision fact"],
        "legal_next": ["ACTION_AUTHORIZATION"],
        "forbidden": ["execution", "authorization bypass", "verification bypass"],
    },
    "ACTION_AUTHORIZATION": {
        "allowed_input": "validated action + deterministic context (uid, balance, tier, human approval)",
        "responsibility": "deterministic control, default-deny: registry, schema, permission/policy, "
                          "resource/spending constraints, risk, human approval gate",
        "output_facts": ["authorization status (granted|denied|pending_human)", "reason"],
        "legal_next": ["ACTION", "ESCALATION_CHECK", "REASONING"],
        "forbidden": ["accepting model-issued authorization", "executing the action"],
    },
    "ACTION": {
        "allowed_input": "validated + authorized operation (registered action, valid args, granted authz)",
        "responsibility": "actual external execution; at-most-once (idempotent op ledger)",
        "output_facts": ["success/failure", "output", "error", "evidence", "execution metadata"],
        "legal_next": ["VERIFICATION"],
        "forbidden": ["reasoning", "DOP routing", "declaring verification PASS", "responding to the user"],
    },
    "ESCALATION_CHECK": {
        "allowed_input": "run posture facts (non-progress, budget, authorization denial, retries)",
        "responsibility": "pure control evaluation: hard-escalate vs one-more-reasoning-pass vs respond",
        "output_facts": ["non_progress", "budget_exhausted", "escalate_reason", "retries"],
        "legal_next": ["ESCALATION", "REASONING", "RESPONSE"],
        "forbidden": ["hidden cognition", "model calls", "execution"],
    },
    "ESCALATION": {
        "allowed_input": "hard escalation condition (permission, risk, approval, constraint, uncertainty)",
        "responsibility": "control mechanism: structural escalation record + lifecycle marking",
        "output_facts": ["escalation {reason, run_id}", "lifecycle=escalated"],
        "legal_next": ["RESPONSE"],
        "forbidden": ["hidden reasoning", "natural-language generation", "actions"],
    },
    "CLARIFICATION_REQUIRED": {
        "allowed_input": "clarification question (semantic perception fact) + optional human answer",
        "responsibility": "wait for explicit human input (runtime pauses; no transition)",
        "output_facts": ["clarification_answered", "clarification_answer"],
        "legal_next": ["RESPONSE"],
        "forbidden": ["self-answering with invented facts", "actions", "routing"],
    },
    "RESPONSE": {
        "allowed_input": "current request + bounded context + verified facts (verification verdict, "
                         "action results, reasoning draft, language fact)",
        "responsibility": "final user-facing output ONLY (single NL generator); reflect verified "
                          "results faithfully; structural pending objects, no canned answers",
        "output_facts": ["response text", "structural pending (clarification|approval)"],
        "legal_next": ["END"],
        "forbidden": ["new actions", "DOP transitions", "distorting verification results",
                      "unverified claims", "restarting orchestration"],
    },
    "END": {
        "allowed_input": "delivered response",
        "responsibility": "terminal — no further work",
        "output_facts": [],
        "legal_next": [],
        "forbidden": ["any transition", "any component call"],
    },
}
