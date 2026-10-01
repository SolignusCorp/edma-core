"""OpenAICompatAdapter unit testlari — FAKE transport (real network YO'Q).

Kalitlar FAQAT env'dan o'qiladi (hech qachon repo/trace/log'ga yozilmaydi).
Round-robin + 429/403/transport'da rotatsiya — SaaS GroqAdapter shartnomasi.
"""
import os

import pytest

from edma_core.bench.adapters.openai_compat import DEFAULT_BASE_URL, OpenAICompatAdapter, keys_from_env


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._p = payload or {}
        self.text = str(self._p)[:120]

    def json(self):
        return self._p


class _FakeHttp:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def post(self, url, json=None, headers=None):
        self.calls.append((headers["Authorization"], json["model"]))
        item = self.script.pop(0) if self.script else self.script[-1]
        if isinstance(item, Exception):
            raise item
        status, payload = item
        return _Resp(status, payload)


def _ok(text='{"a": 1}', pt=10, ct=5):
    return (200, {"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                  "usage": {"prompt_tokens": pt, "completion_tokens": ct}})


def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("EDMA_BENCH_KEY", "EDMA_BENCH_KEYS", "GROQ_KEY_", "GROQ_KEYS",
                         "GROQ_API_KEY")):
            monkeypatch.delenv(k)


def test_keys_from_env_numbered_and_list(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("EDMA_BENCH_KEY_1", "k1")
    monkeypatch.setenv("EDMA_BENCH_KEY_2", "k2")
    monkeypatch.setenv("EDMA_BENCH_KEYS", "k3,k4")
    assert keys_from_env() == ["k1", "k2", "k3", "k4"]


def test_no_keys_raises(monkeypatch):
    _clean_env(monkeypatch)
    with pytest.raises(RuntimeError, match="BENCH_KEYS_EMPTY"):
        OpenAICompatAdapter()


def test_round_robin_and_default_model():
    http = _FakeHttp([_ok(), _ok()])
    a = OpenAICompatAdapter(api_keys=["KA", "KB"], client=http)
    a.generate([{"role": "user", "content": "x"}])
    a.generate([{"role": "user", "content": "x"}])
    assert [c[0] for c in http.calls] == ["Bearer KA", "Bearer KB"]
    # standing qaror: benchmark default model gpt-oss EMAS (qwen) — hech qanday
    # koddagi default gpt-oss'ga ishorA qilmaydi:
    assert "gpt-oss" not in a.model_id
    assert DEFAULT_BASE_URL.startswith("https://")


def test_rotation_on_429_rate_limit():
    http = _FakeHttp([(429, {"error": {"message": "Rate limit reached"}}), _ok("ok")])
    a = OpenAICompatAdapter(api_keys=["KA", "KB"], client=http)
    assert a.generate([{"role": "user", "content": "x"}]).text == "ok"
    assert len(http.calls) == 2 and a.key_errors[0] == "http_429"


def test_rotation_on_transport_error():
    http = _FakeHttp([TimeoutError("t"), _ok("ok")])
    a = OpenAICompatAdapter(api_keys=["KA", "KB"], client=http)
    assert a.generate([{"role": "user", "content": "x"}]).text == "ok"
    assert len(http.calls) == 2 and a.key_errors[0] == "transport: TimeoutError"


def test_metadata_no_key_material():
    a = OpenAICompatAdapter(api_keys=["SECRETVALUE1"], client=_FakeHttp([]))
    assert "SECRETVALUE" not in str(a.metadata())


def test_all_exhausted_raises():
    http = _FakeHttp([(429, {"error": {"message": "Rate limit"}})] * 4)
    a = OpenAICompatAdapter(api_keys=["A", "B"], max_rounds=2, client=http)
    with pytest.raises(RuntimeError, match="BENCH_ALL_KEYS_EXHAUSTED"):
        a.generate([{"role": "user", "content": "x"}])


def test_400_tool_use_failed_is_retryable():
    """Provider native tool-call transiyent rad — retry (model kontenti emas)."""
    http = _FakeHttp([
        (400, {"error": {"message": "Tool choice is none, but model called a tool",
                         "code": "tool_use_failed"}}),
        _ok("ok"),
    ])
    a = OpenAICompatAdapter(api_keys=["KA", "KB"], client=http)
    assert a.generate([{"role": "user", "content": "x"}]).text == "ok"
    assert len(http.calls) == 2 and a.key_errors[0] == "http_400_tool_use_failed"


def test_400_other_raises_bad_request():
    http = _FakeHttp([(400, {"error": {"message": "bad param"}})])
    a = OpenAICompatAdapter(api_keys=["KA"], client=http)
    with pytest.raises(RuntimeError, match="BENCH_BAD_REQUEST"):
        a.generate([{"role": "user", "content": "x"}])


def test_404_model_not_found_is_stop_not_retry():
    http = _FakeHttp([(404, {"error": {"message": "model not found"}})])
    a = OpenAICompatAdapter(api_keys=["KA"], client=http)
    with pytest.raises(RuntimeError, match="MODEL_NOT_FOUND"):
        a.generate([{"role": "user", "content": "x"}])
    assert len(http.calls) == 1  # 404 — rotatsiya YO'Q (provayder javob bermadi → STOP)
