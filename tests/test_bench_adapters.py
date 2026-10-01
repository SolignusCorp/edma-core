"""§3/§18 ADAPTER testlari — BenchmarkModelAdapter interfeysi + mock determinism."""


import pytest

from edma_core.bench.adapters.base import BenchmarkModelAdapter, GenResult
from edma_core.bench.adapters.mock import MockModelAdapter
from edma_core.bench.adapters.tools import ScriptedToolExecutor
from edma_core.bench.control import parse_action


def test_adapter_is_abstract():
    with pytest.raises(TypeError):
        BenchmarkModelAdapter()


def test_mock_fifo_turns():
    m = MockModelAdapter(["bir", "ikki", "uch"])
    assert m.generate([{"role": "user", "content": "x"}]).text == "bir"
    assert m.generate([{"role": "user", "content": "x"}]).text == "ikki"
    assert m.generate([{"role": "user", "content": "x"}]).text == "uch"


def test_mock_repeats_last_turn_deterministically():
    """Script tugagach oxirgi turn TAKTORLANADI (hujjatlashtirilgan, deterministik)."""
    m = MockModelAdapter(["faqat bitta"])
    m.generate([{"role": "user", "content": "x"}])
    assert m.generate([{"role": "user", "content": "x"}]).text == "faqat bitta"


def test_mock_stream_returns_same_text():
    m = MockModelAdapter(["javob"])
    assert "".join(m.stream([{"role": "user", "content": "x"}])) == "javob"


def test_mock_metadata():
    m = MockModelAdapter(["a"], model_id="test-model")
    meta = m.metadata()
    assert meta["model_id"] == "test-model"
    assert meta["adapter"] == "mock"


def test_tool_executor_fifo_and_exhausted():
    ex = ScriptedToolExecutor({"web.search": [{"ok": True, "output": {"r": 1}, "evidence": {"e": 1}}]})
    r1 = ex.execute("web.search", {"query": "x"})
    assert r1["ok"] is True
    r2 = ex.execute("web.search", {"query": "x"})
    assert r2["ok"] is False
    assert r2["error"] == "tool_script_exhausted"
    assert len(ex.records()) == 2


def test_tool_executor_unknown_type():
    ex = ScriptedToolExecutor({})
    r = ex.execute("nope.tool", {})
    assert r["ok"] is False and r["error"] == "unregistered_tool_type"


def test_tool_executor_records_shape():
    ex = ScriptedToolExecutor({"web.search": [{"ok": True, "output": {"r": 1}, "evidence": {"e": 1}}]})
    ex.execute("web.search", {"query": "q"})
    rec = ex.records()[0]
    assert rec["type"] == "web.search" and rec["executed"] is True and rec["args"] == {"query": "q"}


def test_parse_action_json_proposal():
    a = parse_action('{"propose_action": {"type": "web.search", "args": {"query": "x"}}}')
    assert a == {"type": "web.search", "args": {"query": "x"}}
    assert parse_action("oddiy matn") is None
    assert parse_action('{"boshqa": 1}') is None


def test_gen_result_shape():
    g = GenResult(text="t", usage={"input_tokens": 1, "output_tokens": 2})
    assert g.usage["output_tokens"] == 2


# ── CONTROL protokoli (§2) — real-model uchun mustahkam ──

def test_parse_action_from_prose_wrapped_json():
    from edma_core.bench.control import parse_action
    txt = 'AVAILABLE TOOLS:\n- web.search\n\n{"propose_action": {"type": "web.search", "args": {"query": "x"}}}'
    assert parse_action(txt) == {"type": "web.search", "args": {"query": "x"}}


def test_parse_action_pure_json_still_works():
    from edma_core.bench.control import parse_action
    assert parse_action('{"propose_action": {"type": "a", "args": {}}}')["type"] == "a"
    assert parse_action("oddiy matn") is None


def test_control_system_prompt_lists_exact_tool_names():
    from edma_core.bench.control import _system_prompt_for
    c = {"available_tools": ["web.search", "dataset.create"]}
    sp = _system_prompt_for(c)
    assert "AVAILABLE TOOLS" in sp and "- web.search" in sp and "- dataset.create" in sp
    assert _system_prompt_for({"available_tools": []}) == sp.split("AVAILABLE TOOLS")[0].rstrip() \
        or "AVAILABLE TOOLS" not in _system_prompt_for({"available_tools": []})
