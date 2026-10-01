"""§4 CASE SCHEMA testlari + same-input invariant + hash determinism."""
import copy

import pytest

from edma_core.bench.cases.loader import load_golden_cases, load_golden_scripts
from edma_core.bench.schemas import (
    REQUIRED_FIELDS,
    CaseSchemaError,
    canonical_input,
    case_set_hash,
    input_hash,
    validate_case,
)


def test_all_16_fields_required():
    assert len(REQUIRED_FIELDS) == 16
    assert "case_id" in REQUIRED_FIELDS and "tags" in REQUIRED_FIELDS


def test_golden_set_valid_and_categories_A_to_O():
    cases = load_golden_cases()
    assert len(cases) == 15
    assert [c["category"] for c in cases] == [chr(65 + i) for i in range(15)]
    for c in cases:
        validate_case(c)


def test_missing_field_raises():
    c = load_golden_cases()[0]
    bad = {k: v for k, v in c.items() if k != "tags"}
    with pytest.raises(CaseSchemaError):
        validate_case(bad)


def test_bad_category_raises():
    c = copy.deepcopy(load_golden_cases()[0])
    c["category"] = "Z"
    with pytest.raises(CaseSchemaError):
        validate_case(c)


def test_bad_criteria_kind_raises():
    c = copy.deepcopy(load_golden_cases()[0])
    c["success_criteria"] = [{"kind": "vibes", "dim": "x"}]
    with pytest.raises(CaseSchemaError):
        validate_case(c)


def test_custom_predicate_required():
    c = copy.deepcopy(load_golden_cases()[0])
    c["success_criteria"] = [{"kind": "custom"}]
    with pytest.raises(CaseSchemaError):
        validate_case(c)


def test_same_input_invariant_canonical():
    """§2: control va EDMA AYNAN shu canonical input'ni oladi."""
    c = load_golden_cases()[0]
    can = canonical_input(c, "SYS")
    assert can == {"system_prompt": "SYS", "context": c["conversation_context"],
                   "task": c["input"], "toolset": sorted(c["available_tools"])}
    # toolset tartibi kanonik:
    c2 = copy.deepcopy(c)
    c2["available_tools"] = list(reversed(c["available_tools"]))
    assert canonical_input(c2, "SYS") == can


def test_input_hash_deterministic():
    c = load_golden_cases()[0]
    h1 = input_hash(canonical_input(c, "SYS"))
    assert h1 == input_hash(canonical_input(c, "SYS"))
    assert h1 != input_hash(canonical_input(c, "SYS2"))


def test_case_set_hash_order_independent():
    cases = load_golden_cases()
    h1 = case_set_hash(cases)
    h2 = case_set_hash(list(reversed(cases)))
    assert h1 == h2


def test_scripts_cover_all_cases():
    cases = load_golden_cases()
    scripts = load_golden_scripts()
    assert set(scripts) == {c["case_id"] for c in cases}
