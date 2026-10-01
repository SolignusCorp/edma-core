"""EDMA BINDING testlari — AdapterCognitiveModel + extract_json (real-model ko'prigi)."""
import asyncio

from edma_core.bench.adapters.edma_binding import AdapterCognitiveModel, extract_json
from edma_core.bench.adapters.mock import MockModelAdapter


def test_extract_json_plain():
    assert extract_json('{"a": 1}')["a"] == 1


def test_extract_json_fenced():
    assert extract_json('```json\n{"a": 1}\n```')["a"] == 1


def test_extract_json_with_chatter():
    assert extract_json('Javob: {"b": [1, 2]} umuman')["b"] == [1, 2]


def test_extract_json_nested():
    assert extract_json('{"x": {"y": 2}}')["x"]["y"] == 2


def test_extract_json_none_when_no_json():
    assert extract_json("hech qanday JSON yo'q") is None
    assert extract_json("") is None


def test_adapter_model_parses_component_json():
    m = AdapterCognitiveModel(MockModelAdapter(['{"intent": "math", "requires_clarification": false}']))
    r = asyncio.run(m.call("perceive", {"messages": [{"role": "user", "content": "2+2?"}]}))
    assert r.ok is True and r.data["intent"] == "math"
    assert m.calls == 1 and m.usage["prompt_tokens"] > 0


def test_adapter_model_unparseable_is_honest_error():
    m = AdapterCognitiveModel(MockModelAdapter(["proza, JSON emas"]))
    r = asyncio.run(m.call("reason", {"messages": []}))
    assert r.ok is False and r.error == "model_json_unparseable"


def test_adapter_model_mode_prompts_present():
    for mode in ("perceive", "energy", "reason", "respond"):
        assert mode in AdapterCognitiveModel.MODE_PROMPTS


def test_harness_accepts_cognitive_model_override():
    """EdmaHarness.run(cognitive_model=...) — scripted o'rniga real ko'prik."""
    from edma_core.bench.adapters.edma_binding import EdmaHarness
    from edma_core.bench.cases.loader import load_golden_cases
    case = next(c for c in load_golden_cases() if c["case_id"] == "A01_direct_math")
    cog = AdapterCognitiveModel(MockModelAdapter([
        '{"intent": "math", "requires_clarification": false, "effort_hint": "low", "action_candidate": "none"}',
        '{"ready": true, "response_draft": "8"}',
        '{"text": "8"}',
    ]))
    art = EdmaHarness().run(case, cognitive_model=cog)
    assert art["final_text"] == "8" and art["model_calls"] == 3
    assert (art["dop_path"] or [])[-1] == "RESPONSE"


def test_respond_scalar_text_is_normalized_not_invented():
    """Real model respond uchun JSON emas, yalit matn qaytarsa — butun matn javobdir
    (deterministik normallashtirish; hech qanday qo'shimcha semantic IXTIRO YO'Q)."""
    m = AdapterCognitiveModel(MockModelAdapter(["8"]))
    r = asyncio.run(m.call("respond", {"messages": []}))
    assert r.ok is True and r.data == {"text": "8"}


def test_reason_non_json_retries_once_with_reminder():
    """reason non-JSON → BIR marta JSON-eslatma bilan qayta so'riladi (protocol retry)."""
    m = AdapterCognitiveModel(MockModelAdapter(["8", '{"ready": true, "response_draft": "8"}']))
    r = asyncio.run(m.call("reason", {"messages": []}))
    assert r.ok is True and r.data["ready"] is True
    assert m.calls == 2  # asosiy + eslatma


def test_reason_twice_unparseable_is_honest_error():
    m = AdapterCognitiveModel(MockModelAdapter(["8", "yana proza"]))
    r = asyncio.run(m.call("reason", {"messages": []}))
    assert r.ok is False and r.error == "model_json_unparseable"
    assert m.calls == 2
