"""WORLD-V1 grading testlari — dataset REFERENCE kalitlari bilan deterministik."""

from edma_core.bench import world
from edma_core.bench.cases.loader import load_world_set
from edma_core.bench.world import grade, last_number, wilson


def test_world_set_structure():
    w = load_world_set("world-v1")
    assert w["set_version"] == "world-v1"
    assert len(w["items"]) == 30
    assert sum(1 for i in w["items"] if i["dataset"] == "gsm8k") == 15
    assert sum(1 for i in w["items"] if i["dataset"] == "mmlu") == 15
    for i in w["items"]:
        assert i["reference"] and i["question"]
        if i["grading"] == "mc":
            assert len(i["choices"]) == 4 and i["reference"] in "ABCD"


def test_last_number():
    assert last_number("x = 5 va jami 42 dona") == 42.0
    assert last_number("javob: 1,234") == 1234.0
    assert last_number("raqam yo'q") is None


def test_grade_numeric_exact_and_extraction():
    item = {"grading": "numeric", "reference": "42"}
    assert grade(item, "yechim... #### 42") is True
    assert grade(item, "yechim... 3 + 39 = 42") is True     # oxirgi raqam
    assert grade(item, "javob: 43") is False
    assert grade(item, "bo'sh") is False


def test_grade_mc_letter():
    item = {"grading": "mc", "reference": "B"}
    assert grade(item, "B") is True
    assert grade(item, "Javob: B") is True
    assert grade(item, "Men C ni tanlayman") is False
    assert grade(item, "") is False


def test_wilson_ci():
    lo, hi = wilson(0.5, 30)
    assert 0.30 < lo < 0.50 < hi < 0.70
    lo, hi = wilson(0.846, 13)
    assert lo < 0.846 < hi


def test_wilson_zero_n():
    assert wilson(0.0, 0) == (0.0, 0.0)


def test_run_world_set_mock_counts_honestly():
    """run_world_set: FAQAT FAKTY — reference'lardan olingan javoblar bilan 4/4,
    keyin noto'g'ri javoblar bilan 0/4; hech narsa yashirilmaydi."""
    from edma_core.bench.adapters.mock import MockModelAdapter
    w = load_world_set("world-v1")["items"][:4]
    good = [i["reference"] if i["grading"] == "mc" else f"#### {i['reference']}" for i in w]
    a = MockModelAdapter(list(good) + ["javob yo'q", "javob yo'q", "javob yo'q", "javob yo'q"])
    out = world.run_world_set(a, name="world-v1", limit=4, mode_name="mock")
    assert out["n"] == 4 and out["correct"] == 4
    assert all(p["ok"] for p in out["per_item"])
    assert 0.0 <= out["ci95"][0] <= out["rate"] <= out["ci95"][1] <= 1.0
    b = MockModelAdapter(["javob yo'q", "javob yo'q", "javob yo'q", "javob yo'q"])
    out2 = world.run_world_set(b, name="world-v1", limit=4, mode_name="mock")
    assert out2["correct"] == 0 and not any(p["ok"] for p in out2["per_item"])
