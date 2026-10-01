"""EDMA Verification tests — evidence-based verdicts; SKIPPED != PASS; read-back."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from edma_core import verification as V


# ------------------------------------------------------------- actions
def test_search_real_results_pass():
    r = {"ok": True, "action": "web.search", "output": {},
         "evidence": {"provider": "tavily", "results": [
             {"title": "t", "url": "https://example.com", "content": "c"}]}}
    assert V.verify_action("web.search", r, "u1").result == V.PASS


def test_search_mock_is_uncertain_never_pass():
    r = {"ok": True, "action": "web.search", "output": {},
         "evidence": {"provider": "mock", "results": [
             {"title": "simulated", "url": "https://example.com/sim", "content": "sim"}]}}
    v = V.verify_action("web.search", r, "u1")
    assert v.result == V.UNCERTAIN


def test_search_no_results_fail():
    r = {"ok": False, "action": "web.search", "output": {},
         "evidence": {"provider": "duckduckgo", "results": []}}
    assert V.verify_action("web.search", r, "u1").result == V.FAIL


def test_dataset_readback(monkeypatch):
    import edma_core.verification as vi
    class FakeStore:
        def get_dataset(self, ds_id, uid):
            if ds_id == "ds_ok":
                return {"id": ds_id, "examples": [{"messages": []}] * 5}
            return None
    monkeypatch.setattr(vi, "_store", FakeStore())
    ok = {"ok": True, "action": "dataset.create", "output": {"dataset_id": "ds_ok", "samples": 5},
          "evidence": {"dataset_id": "ds_ok", "samples": 5}}
    assert V.verify_action("dataset.create", ok, "u1").result == V.PASS
    missing = {"ok": True, "action": "dataset.create", "output": {"dataset_id": "ds_gone", "samples": 5},
               "evidence": {"dataset_id": "ds_gone", "samples": 5}}
    assert V.verify_action("dataset.create", missing, "u1").result == V.FAIL
    mismatch = {"ok": True, "action": "dataset.create", "output": {"dataset_id": "ds_ok", "samples": 9},
                "evidence": {"dataset_id": "ds_ok", "samples": 9}}
    assert V.verify_action("dataset.create", mismatch, "u1").result == V.FAIL


def test_failed_dataset_creation_is_fail_not_pass():
    r = {"ok": False, "action": "dataset.create", "output": {}, "evidence": {},
         "error": "tier_limit: max 50 samples"}
    assert V.verify_action("dataset.create", r, "u1").result == V.FAIL


def test_unknown_action_uncertain_not_pass():
    assert V.verify_action("made.up", {"ok": True}, "u1").result == V.UNCERTAIN
