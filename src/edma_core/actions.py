"""EDMA Action Registry + deterministic ACTION_AUTHORIZATION.

Task §16/§17:
- Actions must be explicitly registered (name, schema, risk, approval rule,
  handler, evidence). Unregistered action types are DEFAULT-DENIED.
- The model cannot authorize itself: `authorized`/`approved` fields in model
  output are ignored by construction (they never reach authorization inputs).
- No eval/exec/shell/arbitrary subprocess — handlers are explicit, bounded,
  dependency-injected functions (see edma_core.contrib for factories).
- Human approval is an API-level pause: runs park at ACTION_AUTHORIZATION with
  status=pending_human until the caller answers (approve/deny through the
  runtime — never through model output).

CORE REGISTRY contains only `web.fetch` (pure, bounded, read-only).
Capability actions (web.search, dataset.create, ...) are PLUGIN factories in
`edma_core.contrib` — dependency-injected, never imported by the core.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

SEARCH_COST = 0.002  # reference price for a web.search call (plugins may override)

ALLOWED_LANGS = ("en", "uz", "ru", "tr", "es", "de", "fr")


# ---------------------------------------------------------------- registry
@dataclass
class ActionSpec:
    name: str
    description: str
    schema: dict                      # arg -> (type, min, max, required)
    risk: str                         # low | medium | high
    requires_human: bool              # deterministic approval gate (model cannot change it)
    cost_estimate: float              # informational; real gate = wallet preflight
    handler: Callable                 # (uid, args, ctx) -> dict {ok, output..., evidence...}


@dataclass
class AuthzDecision:
    status: str                       # granted | denied | pending_human
    reason: str
    action: str = ""
    args: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"status": self.status, "reason": self.reason,
                "action": self.action, "risk": ACTION_REGISTRY.get(self.action).risk if self.action in ACTION_REGISTRY else None,
                "requires_human": self.action in ACTION_REGISTRY and ACTION_REGISTRY[self.action].requires_human}


@dataclass
class ActionResult:
    ok: bool
    action: str
    output: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {"ok": self.ok, "action": self.action, "output": self.output,
                "evidence": {k: (v if k != "results" else v[:3]) for k, v in self.evidence.items()},
                "error": self.error}


# ---------------------------------------------------------------- handlers
# (capability handlers live in edma_core.contrib as injected factories)

_FETCH_MAX_BYTES = 512 * 1024


def _h_web_fetch(uid: str, args: dict, ctx: dict) -> dict:
    """CRITICAL-4: ro'yxatdan o'tgan xavfsiz sahifa o'qish (web.fetch).

    Chegara: FAQAT https, timeout 10s, max 512KB, matn kontent-tiplari,
    HTML taglari tozalanadi. Hech qanday exec/fs yozuv YO'Q. Idempotent
    (read-only) — qayta urinish xavfsiz. Dalil: url+status+sha16+uzunlik.
    """
    import httpx as _hx
    url = str(args.get("url", "")).strip()
    if not url.startswith("https://") or len(url) > 500:
        return {"ok": False, "error": "invalid_url", "evidence": {"url_given": url[:120]}}
    max_chars = int(args.get("max_chars", 2000))
    try:
        with _hx.Client(timeout=10.0, follow_redirects=True,
                        headers={"User-Agent": "SolignusAI-EDMA/1.0 (web.fetch)"}) as cl:
            r = cl.get(url)
    except Exception as e:
        return {"ok": False, "error": f"fetch_failed: {str(e)[:120]}",
                "evidence": {"url": url[:160]}}
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    if r.status_code != 200:
        return {"ok": False, "error": f"http_{r.status_code}",
                "evidence": {"url": url[:160], "status": r.status_code}}
    if ctype and not (ctype.startswith("text/") or ctype in ("application/json", "application/xml")):
        return {"ok": False, "error": f"unsupported_content_type:{ctype}",
                "evidence": {"url": url[:160], "content_type": ctype}}
    raw = r.content[:_FETCH_MAX_BYTES]
    try:
        text = raw.decode(r.encoding or "utf-8", "replace")
    except Exception:
        text = raw.decode("utf-8", "replace")
    import re as _re
    if "html" in ctype:
        text = _re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=_re.S | _re.I)
        text = _re.sub(r"<[^>]+>", " ", text)
        text = _re.sub(r"\s+", " ", text)
    text = text.strip()[:max(200, min(int(max_chars), 4000))]
    if not text:
        return {"ok": False, "error": "empty_content", "evidence": {"url": url[:160]}}
    sha = hashlib.sha1(raw).hexdigest()[:16]
    return {"ok": True, "url": url[:160], "text": text,
            "evidence": {"url": url[:160], "status": r.status_code, "sha16": sha,
                         "chars": len(text), "content_type": ctype or "unknown"}}


ACTION_REGISTRY: dict[str, ActionSpec] = {
    "web.fetch": ActionSpec(
        name="web.fetch", description="Read one https page (bounded, read-only, evidence-hashed).",
        schema={"url": (str, 9, 500, True), "max_chars": (int, 200, 4000, False)},
        risk="medium", requires_human=False, cost_estimate=0.0, handler=_h_web_fetch),
}


def register(spec: ActionSpec) -> None:
    """Register (or replace) an action capability. Deterministic gate stays."""
    ACTION_REGISTRY[spec.name] = spec


def unregister(name: str) -> bool:
    return ACTION_REGISTRY.pop(name, None) is not None


# ---------------------------------------------------------------- validation / authz / exec
def validate_args(spec: ActionSpec, args: dict) -> tuple[bool, dict, Optional[str]]:
    """Deterministic schema validation. -> (ok, clean_args, error)"""
    if not isinstance(args, dict):
        return False, {}, "args_not_object"
    clean: dict = {}
    for arg, (typ, mn, mx, required) in spec.schema.items():
        raw = args.get(arg)
        if raw is None or (isinstance(raw, str) and not raw.strip() and typ is str and required):
            if required:
                return False, {}, f"missing_arg:{arg}"
            continue
        if typ is int:
            try:
                v = int(raw)
            except Exception:
                return False, {}, f"bad_type:{arg}"
            if v < mn or v > mx:
                return False, {}, f"out_of_range:{arg}"
            clean[arg] = v
        else:
            v = str(raw).strip()
            if len(v) < mn or len(v) > mx:
                return False, {}, f"out_of_range:{arg}"
            if spec.name == "web.search" and arg == "query":
                v = re.sub(r"\s+", " ", v)
            clean[arg] = v
    return True, clean, None


def authorize(action_type: Optional[str], args: dict, ctx: dict) -> AuthzDecision:
    """ACTION_AUTHORIZATION semantics — deterministic, default-deny.

    ctx: {uid, balance_fn, tier_spec_fn, run_id, approval: 'granted'|'denied'|None}
    The model's opinion is not an input. Human approval arrives only through the
    runtime (caller answered a pending-approval pause), never from model output.
    """
    spec = ACTION_REGISTRY.get(action_type or "")
    if spec is None:
        return AuthzDecision("denied", "default_deny: unregistered action", action=str(action_type))
    ok, clean, err = validate_args(spec, args)
    if not ok:
        return AuthzDecision("denied", f"invalid_args: {err}", action=spec.name)
    balance_fn = ctx.get("balance_fn")
    if spec.cost_estimate > 0 and callable(balance_fn):
        try:
            if float(balance_fn()) < spec.cost_estimate:
                return AuthzDecision("denied", "insufficient_balance", action=spec.name)
        except Exception:
            return AuthzDecision("denied", "balance_check_failed", action=spec.name)
    tier_fn = ctx.get("tier_spec_fn")
    if spec.name == "dataset.create" and callable(tier_fn):  # plugin-provided cap
        try:
            if int(clean.get("size", 30)) > int(tier_fn()["max_samples"]):
                return AuthzDecision("denied", "tier_sample_cap", action=spec.name)
        except Exception:
            return AuthzDecision("denied", "tier_check_failed", action=spec.name)
    if spec.requires_human:
        approval = ctx.get("approval")
        if approval == "granted":
            return AuthzDecision("granted", "human_approved", action=spec.name)
        if approval == "denied":
            return AuthzDecision("denied", "human_denied", action=spec.name)
        return AuthzDecision("pending_human", "high-risk action awaits explicit human approval",
                             action=spec.name)
    return AuthzDecision("granted", f"auto (risk={spec.risk})", action=spec.name)


def execute(action_type: str, args: dict, ctx: dict) -> ActionResult:
    """Execute a REGISTERED action. Validation is re-done here — never trust the caller."""
    spec = ACTION_REGISTRY.get(action_type or "")
    if spec is None:
        return ActionResult(False, str(action_type), error="default_deny: unregistered action")
    ok, clean, err = validate_args(spec, args)
    if not ok:
        return ActionResult(False, spec.name, error=f"invalid_args: {err}")
    try:
        out = spec.handler(ctx.get("uid"), clean, ctx)
    except Exception as e:
        return ActionResult(False, spec.name, error=f"handler_exception: {str(e)[:200]}")
    ev = out.pop("evidence", None) or {}
    return ActionResult(bool(out.get("ok")), spec.name, output=out, evidence=ev,
                        error=out.get("error"))


def list_actions() -> list[dict]:
    return [{"name": s.name, "description": s.description, "risk": s.risk,
             "requires_human": s.requires_human, "cost_estimate": s.cost_estimate,
             "schema": {k: {"type": v[0].__name__, "min": v[1], "max": v[2], "required": v[3]}
                        for k, v in s.schema.items()}}
            for s in ACTION_REGISTRY.values()]
