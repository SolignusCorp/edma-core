"""ZERO-TEMPLATE invariant (core qismi): javob FAQAT modeldan.

SaaS'ga xos testlar (chat API, frontend) platforma repoda qoladi; bu yerda
faqat core fayllari va runtime darajasidagi halollik tekshiriladi.
"""
import asyncio
import json
import os
import re
import sys

import pytest

from edma_core import runtime as RT
from edma_core import dop
from edma_core.components import heuristic_perceive
from tests.test_edma_runtime import env, FakeModel, run_edma  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_FILES = sorted(
    os.path.join(dp, f) for dp, _, fs in os.walk(os.path.join(ROOT, "src", "edma_core"))
    for f in fs if f.endswith(".py")
)


def _src_clean(path: str) -> str:
    src = open(path, encoding="utf-8").read()
    src = re.sub(r'"""(?:.|\n)*?"""', " ", src)
    src = re.sub(r"'''(?:.|\n)*?'''", " ", src)
    src = re.sub(r"#[^\n]*", " ", src)
    return src.lower()


BANNED_TEMPLATES = (
    "i don't have the ability", "as an ai language model", "lorem ipsum",
)
# Izoh: 'vazifa yakunlanmadi' / 'aniqlashtiruvchingiz' satrlari dataset_exec.py'dagi
# _QC_BANNED detektor ro'yxatida — ular canned javob EMAS, aksinta canned-ni RAD
# QILUVCHI filtrlardir. Shu sababli umumiy skandan chiqarilgan va alohida tekshiriladi:


def test_qc_banned_list_is_detector_not_template():
    src = open(os.path.join(ROOT, "src", "edma_core", "dataset_exec.py"), encoding="utf-8").read()
    assert "_QC_BANNED" in src  # rad-etuvchi QC ro'yxati turishi shart
    for token in ("vazifa yakunlanmadi", "aniqlashtiruvchingiz"):
        assert token in src  # detektor sifatida mavjud
        # va bu tokenlar hech qanday JAVOB matni sifatida ishlatilmaydi:
        assert src.count(token) == 1


def test_core_has_no_canned_user_facing_templates():
    hits = []
    for f in CORE_FILES:
        src = _src_clean(f)
        for b in BANNED_TEMPLATES:
            if b in src:
                hits.append((os.path.basename(f), b))
    assert not hits, f"canned template qoldi: {hits}"


def test_unknown_entity_no_special_branch():
    """Noma'lum entity uchun maxsus branch/regex YO'Q — javob faqat modeldan."""
    assert not hasattr(RT, "_unknown_entity") and not hasattr(RT, "_UNKNOWN_RX")
    comp_src = _src_clean(os.path.join(ROOT, "src", "edma_core", "components.py"))
    rt_src = _src_clean(os.path.join(ROOT, "src", "edma_core", "runtime.py"))
    for entity in ("faable", "qorovul 8472", "xylaron", "zentavo"):
        for src, name in ((comp_src, "components"), (rt_src, "runtime")):
            assert entity not in src, f"{name}: noma'lum entity uchun maxsus holat!"
        out = heuristic_perceive({"messages": [{"role": "user", "content": "Faable 5 nima?"}]})
        assert out["action_candidate"] == "none"


def test_unknown_entity_full_run_model_answered(env):
    """Noma'lum entity: model o'z bilmasligini HALOL aytadi — template YO'Q."""
    ms, mp = env
    view, run = run_edma(env, "uZT", "Faable 5 nima?", always={
        "perceive": {"intent": "question", "requires_clarification": False,
                     "action_candidate": "none", "effort_hint": "low"},
        "energy": {"complexity": "low"},
        "reason": {"ready": True, "response_draft": "d",
                   "knowledge_assessment": {"status": "UNKNOWN", "confidence": 0.05,
                                            "external_information_needed": False},
                   "action_needed": False},
        "respond": {"text": "Faable 5 haqida ishonchli ma'lumotim yo'q — tasdiqlay olmayman."}})
    assert view["status"] == "complete"
    assert "Faable 5" in view["response"] and "yo'q" in view["response"]
    assert view["dop_path"][-1] == "RESPONSE"


def test_heuristic_never_writes_answer_text():
    """Heuristic fallback hech qachon NL javob ixtiro qilmaydi (bo'sh draft)."""
    from edma_core.components import CognitiveModel
    m = CognitiveModel(model="test")
    out = m.heuristic("respond", {"messages": [{"role": "user", "content": "salom"}]}, {})
    assert out.data.get("text") == ""


def test_respond_contract_single_generator():
    """RESPONSE — yagona NL generatori: boshqa komponent f.response_text
    ni precomposed NL bilan to'ldirmaydi."""
    src = _src_clean(os.path.join(ROOT, "src", "edma_core", "runtime.py"))
    assert 'f.response_text = (' not in src
    assert "_respond_generate" in src
