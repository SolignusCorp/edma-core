"""ToolExecutor — CONTROL va EDMA uchun BIR XIL tool dunyosi (§2 same tools).

Hozir ScriptedToolExecutor (deterministik); keyinroq real tool backend ulanadi.
"""
from __future__ import annotations


class ToolResult(dict):
    @property
    def ok(self) -> bool:
        return bool(self.get("ok"))

    @property
    def has_evidence(self) -> bool:
        ev = self.get("evidence") or {}
        return bool(ev.get("results"))


class ScriptedToolExecutor:
    """Har tool turiga ketma-ket natijalar ro'yxati (case scenario'dan)."""
    def __init__(self, scripts: dict | None = None):
        # scripts: {"web.search": [ {ok, output, evidence}, ... ], ...}
        self.scripts = {k: list(v) for k, v in (scripts or {}).items()}
        self.executed: list[dict] = []

    def execute(self, tool_type: str, args: dict) -> ToolResult:
        if tool_type not in self.scripts:
            res = {"ok": False, "error": "unregistered_tool_type",
                   "output": {}, "evidence": {}}
        else:
            seq = self.scripts[tool_type]
            if seq:
                res = dict(seq.pop(0))
            else:  # script tugasa — halol bo'sh natija
                res = {"ok": False, "error": "tool_script_exhausted", "output": {}, "evidence": {}}
        rec = {"type": tool_type, "args": args, "ok": bool(res.get("ok")),
               "executed": True,
               "evidence_present": bool((res.get("evidence") or {}).get("results")
                                        or (res.get("evidence") or {}).get("dataset_id")
                                        or res.get("evidence"))}
        self.executed.append(rec)
        return ToolResult(res)

    def records(self) -> list:
        return [dict(r) for r in self.executed]
