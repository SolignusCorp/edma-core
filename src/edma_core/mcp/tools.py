"""§MCP TOOLS — EDMA verification + action-authorization qatlamini standart
MCP tool sifatida ochish (tashqi agentlar: Claude Desktop, Cursor, ...).

XAVFSIZLIK DIZAYNI (deterministik, default-deny — o'zgartirilmasdan):
  - bu server hech qachon action IJRO ETMAYDI: authorize = QAROR faqat
    (executed:false har javobda), verify = dalil bahosi, list/graph = introspektsiya.
  - model o'z-o'zini authorize QILA OLMAAYDI: approval faqat explicit inson
    qarori sifatida uzatiladi (runtime pending-approval yo'li kabi).
  - NO EVIDENCE -> NO PASS: verify uchun kirit MAJBURIY ActionResult dict
    (edma.actions.execute kuzatiladigan natijasi); model da'vosi dalil EMAS.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from edma_core import actions as _A
from edma_core import dop as _dop
from edma_core import verification as _V

TOOL_NAMES = frozenset({"edma_authorize", "edma_verify",
                        "edma_list_actions", "edma_dop_graph"})


@dataclass
class Tool:
    name: str
    description: str
    inputSchema: dict
    handler: Callable[[dict], dict]

    def descriptor(self) -> dict:
        return {"name": self.name, "description": self.description,
                "inputSchema": self.inputSchema}


def _obj(schema: dict, required: list) -> dict:
    return {"type": "object", "properties": schema, "required": required}


# ── handlers ──

def _h_authorize(a: dict) -> dict:
    if not isinstance(a, dict):
        raise ValueError("arguments MUST be an object")
    action_type = a.get("action_type")
    if action_type is not None and not isinstance(action_type, str):
        raise ValueError("action_type MUST be a string (or null → honest default-deny)")
    args = a.get("args") or {}
    if not isinstance(args, dict):
        raise ValueError("args MUST be an object")
    approval = a.get("approval")
    if approval not in (None, "granted", "denied"):
        raise ValueError("approval MUST be 'granted' | 'denied' (explicit human decision)")
    d = _A.authorize(action_type, args, {"uid": str(a.get("uid") or "mcp"),
                                         "approval": approval})
    out = d.to_dict()
    out["executed"] = False
    out["balance_check"] = "skipped (no wallet configured on the MCP server)"
    out["note"] = ("Decision ONLY — EDMA did NOT execute this action. The model cannot "
                   "self-authorize; high-risk actions stay pending until an explicit "
                   "human approval is passed here.")
    return out


def _h_verify(a: dict) -> dict:
    if not isinstance(a, dict):
        raise ValueError("arguments MUST be an object")
    action = a.get("action")
    if not isinstance(action, str) or not action:
        raise ValueError("action MUST be a non-empty string")
    result = a.get("result")
    if not isinstance(result, dict):
        raise ValueError("result MUST be an ActionResult dict from edma.actions.execute "
                         "(observable output + evidence) — a model claim is NOT evidence")
    v = _V.verify_action(action, result, uid=str(a.get("uid") or "mcp"))
    out = v.to_dict()
    out["note"] = ("PASS requires observable evidence (content/hash/read-back); "
                   "model or EDMA self-reports are not evidence; "
                   "UNCERTAIN is never promoted.")
    return out


def _h_list_actions(_a: dict) -> dict:
    return {"actions": _A.list_actions(),
            "note": "default-deny: unregistered actions are ALWAYS denied"}


def _h_dop_graph(_a: dict) -> dict:
    states = sorted(_dop.STATES)
    return {"states": states,
            "legal_transitions": {s: sorted(_dop.legal_targets(s)) for s in states},
            "invariant": ("the DOP Engine is the sole transition authority; "
                          "components/model return facts only")}


def build_edma_tools() -> list:
    return [
        Tool(
            name="edma_authorize",
            description=("Evaluate EDMA's deterministic action-authorization (default-deny) "
                         "for a PROPOSED action. Returns granted | denied | pending_human. "
                         "Decision only — the action is NOT executed."),
            inputSchema=_obj({
                "action_type": {"type": "string",
                                "description": "registered action type (see edma_list_actions)"},
                "args": {"type": "object", "description": "proposed action arguments"},
                "approval": {"type": "string", "enum": ["granted", "denied"],
                             "description": "explicit HUMAN approval decision, not the model's opinion"},
                "uid": {"type": "string", "description": "caller identity for the decision record"},
            }, required=["action_type"]),
            handler=_h_authorize,
        ),
        Tool(
            name="edma_verify",
            description=("EDMA evidence-based verification: PASS/FAIL/UNCERTAIN for an "
                         "executed action's ActionResult dict (observable output + evidence). "
                         "NO EVIDENCE -> NO PASS; simulated/mock results are not evidence."),
            inputSchema=_obj({
                "action": {"type": "string", "description": "executed action type, e.g. web.fetch"},
                "result": {"type": "object",
                           "description": "ActionResult dict from edma.actions.execute"},
                "uid": {"type": "string"},
            }, required=["action", "result"]),
            handler=_h_verify,
        ),
        Tool(
            name="edma_list_actions",
            description="List registered EDMA actions with risk, human-approval flag and arg schema.",
            inputSchema=_obj({}, required=[]),
            handler=_h_list_actions,
        ),
        Tool(
            name="edma_dop_graph",
            description=("Introspect EDMA's frozen 13-state DOP graph: states and the "
                         "legal transition table."),
            inputSchema=_obj({}, required=[]),
            handler=_h_dop_graph,
        ),
    ]
