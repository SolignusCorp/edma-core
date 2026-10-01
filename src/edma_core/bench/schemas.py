"""§4 BENCHMARK CASE SCHEMA — machine-readable, version-controlled.

Case'lar benchmark'ning MULKI — EDMA natijasiga qarab o'zgartirilmaydi (§15).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

CRITERIA_KINDS = ("exact", "contains", "numeric", "structured", "action",
                  "verification", "safety", "clarification", "recovery", "custom")
DIMS = ("correctness", "action", "verification", "safety", "clarification", "recovery")
CATEGORIES = tuple(["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O"])

REQUIRED_FIELDS = ("case_id", "category", "description", "input", "conversation_context",
                   "available_tools", "expected_behavior", "expected_output",
                   "success_criteria", "failure_criteria", "allowed_actions",
                   "forbidden_actions", "verification_requirements",
                   "max_model_calls", "max_latency", "tags")


class CaseSchemaError(ValueError):
    pass


def _check_criteria(criteria: Any, field: str) -> None:
    if not isinstance(criteria, list):
        raise CaseSchemaError(f"{field}: list bo'lishi kerak")
    for c in criteria:
        if not isinstance(c, dict) or c.get("kind") not in CRITERIA_KINDS:
            raise CaseSchemaError(f"{field}: kind {CRITERIA_KINDS} dan bo'lishi kerak: {c}")
        if c.get("kind") == "custom" and not c.get("predicate"):
            raise CaseSchemaError(f"{field}: custom criterion predicate nomi kerak")


def validate_case(case: dict) -> dict:
    if not isinstance(case, dict):
        raise CaseSchemaError("case dict bo'lishi kerak")
    missing = [f for f in REQUIRED_FIELDS if f not in case]
    if missing:
        raise CaseSchemaError(f"missing fields: {missing}")
    if case["category"] not in CATEGORIES:
        raise CaseSchemaError(f"category {case['category']} not in A..O")
    if not isinstance(case["input"], str) or not case["input"].strip():
        raise CaseSchemaError("input: bo'sh bo'lmasin")
    for listfield in ("conversation_context", "available_tools", "allowed_actions",
                      "forbidden_actions", "verification_requirements", "tags"):
        if not isinstance(case[listfield], list):
            raise CaseSchemaError(f"{listfield}: list kerak")
    _check_criteria(case["success_criteria"], "success_criteria")
    _check_criteria(case["failure_criteria"], "failure_criteria")
    if not isinstance(case["max_model_calls"], int) or case["max_model_calls"] < 1:
        raise CaseSchemaError("max_model_calls: int >= 1")
    if not isinstance(case["max_latency"], (int, float)) or case["max_latency"] <= 0:
        raise CaseSchemaError("max_latency: > 0")
    return case


def canonical_input(case: dict, system_prompt: str) -> dict:
    """SAME-INPUT INVARIANT: control va EDMA AYNAN shuni oladi (§2)."""
    return {
        "system_prompt": str(system_prompt),
        "context": list(case["conversation_context"]),
        "task": case["input"],
        "toolset": sorted(case["available_tools"]),
    }


def input_hash(inp: dict) -> str:
    return hashlib.sha256(json.dumps(inp, sort_keys=True, ensure_ascii=False,
                                     default=str).encode()).hexdigest()[:16]


def case_set_hash(cases: list) -> str:
    # tartibdan mustaqil: case_id bo'yicha normallashtirilgan set identity
    blob = json.dumps(sorted(cases, key=lambda c: str(c.get("case_id"))),
                      sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
