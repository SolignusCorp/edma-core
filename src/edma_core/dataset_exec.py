"""FAST dataset generation — direct model authoring (2026-10-01).

History: samples used to be authored through the full EDMA runtime
(start_and_advance; safe but SLOW — several model calls per sample).
Decision 2026-10-01: both stages go DIRECTLY through the ModelProvider port
(default: groq/qwen — benchmark-validated):

  1) SCENARIO GENERATION — model faqat USER so'rovlarini yozadi
     (til bo'yicha muvozanatli, real inson uslubida, platforma
     terminologiyasi TAQIQLANGAN);
  2) DIRECT ANSWER — one direct model call per scenario; the assistant
     message comes ONLY from the model reply; system_prompt (if given) is
     applied to every sample.

QC: javob bo'sh yoki dastur diagnostikasi/qoldiq belgilarini o'z ichiga
olsa sample KIRITILMAYDI (sabab last_error'da). Dup scenario'lar rad
etiladi. Hech qanday assistant javobi template'dan kelmaydi — model
yozmasa sample yo'q (soxta ma'lumot YARATILMAYDI).

Honest meta: source="llm_direct" — no fabricated dop_path/trace/verification
(those belong to the EDMA runtime path; this path does not use it).
"""
from __future__ import annotations

import re
from typing import Optional

from . import ports

DATASET_LANGS = ("uz", "en", "ru")

# QC: deterministik kod hech qanday NL yozmasligi kerak — assistant matnida
# qoldiq diagnostika/template belgisi ko'rinsa sample rad etiladi.
_QC_BANNED = (
    "[system]", "response_failed", "provider_failed:",
    "action_verification:", "knowledge_assessment:", "execution facts:",
    "vazifa yakunlanmadi", "aniqlashtiruvchingiz", "approval pending",
)

_SCENARIO_CONTRACT = (
    "You generate USER PROMPTS for a dataset. Each prompt is a realistic message a real "
    "person might write to an AI assistant, in the requested language, natural and varied "
    "(short and long, polite and terse). Cover a natural mix of cognitive situations: "
    "known general knowledge, niche/unknown subjects, time-sensitive facts, ambiguous or "
    "underspecified requests, requests that need external search, requests that need no "
    "search, multi-step reasoning, clarifying follow-ups, "
    "recalling what was said, and simple small talk. "
    "STRICT RULES: prompts contain ONLY what a user would say — no references to systems, "
    "runtimes, states, traces, components, models, datasets, prompts or tooling; no "
    "meta-language about testing; no numbering inside the prompt text. "
    'Return ONLY JSON: {"scenarios": ["<user prompt>", ...]} with EXACTLY the requested count.'
)

_MAX_SCENARIO = 300
_MIN_LEN, _MAX_LEN = 5, 500


def _norm_key(text: str) -> str:
    """Semantic-uniqueness gate: punktuatsiya/quti normallashtirish."""
    t = str(text or "").lower()
    t = re.sub(r"[^\w'’\u0400-\u04ff]+", " ", t)
    return " ".join(t.split())[:160]


class Factory:
    """Bir dataset task uchun: scenario generator + to'g'ridan-to'g'ri javob + QC."""

    def __init__(self, uid: str, topic: str, size: int, languages,
                 system_prompt: str = ""):
        if ports.get_provider() is None:
            raise RuntimeError("provider_unavailable")
        self.uid = uid
        self.topic = str(topic or "")[:80]
        self.size = max(1, min(int(size or 30), _MAX_SCENARIO))
        langs = [l for l in (languages or []) if l in DATASET_LANGS]
        self.langs = langs or ["en"]
        self.system_prompt = str(system_prompt or "").strip()[:400]
        self._seen: set[str] = set()
        self._queue: list[tuple[str, str]] = []  # (lang, scenario)
        self.last_error: Optional[str] = None  # halol diagnostika (QC/model sababi)

    async def _model_chat(self, messages: list[dict], max_tokens: int, temperature: float = 0.2):
        """Direct provider call through the ModelProvider port."""
        return await ports.get_provider().chat({"messages": messages,
                                                "max_tokens": max_tokens,
                                                "temperature": temperature})

    # ─────────────── bosqich 1: scenario generation (USER-only) ───────────────
    async def _refill_scenarios(self, need: int) -> None:
        per = max(1, need)
        lang_line = (f"Write every prompt in {self.langs[0]}"
                     + (f" (optionally balance across {', '.join(self.langs[1:])})" if len(self.langs) > 1 else ""))
        prompt = (f"{_SCENARIO_CONTRACT} Topic area: {self.topic or 'any everyday subject'}. {lang_line}. "
                  f"Generate exactly {per} short user prompts — the kind of messages a real person "
                  f"would send to an AI assistant: varied everyday phrasing, natural tone, "
                  f"8-300 characters, no internal terminology, one prompt per scenario. "
                  f'Return ONLY JSON: {{"scenarios": ["<prompt 1>", "<prompt 2>", ...]}} — '
                  f"no prose, no markdown fences around the JSON.")
        res = await self._model_chat([{"role": "user", "content": prompt}],
                                     max_tokens=2000, temperature=1.0)
        if not res or not res.get("ok"):
            self.last_error = f"scenario_model_failed: {(res or {}).get('error') or 'unknown'}"
            return
        raw = res.get("reply")
        items = None
        if isinstance(raw, str):
            # model JSON'ni fence/proza bilan qaytarishi mumkin — platformadagi
            # yagona deterministik parser (json_healer) bilan ochiladi
            try:
                from json_healer import heal as _heal
            except Exception:
                _heal = None
            if _heal is not None:
                parsed = None
                for mode in ("object", "array"):
                    h = _heal(raw, mode)
                    if h.get("ok"):
                        parsed = h.get("value")
                        break
                items = (parsed.get("scenarios") if isinstance(parsed, dict) else parsed)
            else:
                import json as _json
                try:
                    parsed = _json.loads(raw)
                    items = parsed.get("scenarios") if isinstance(parsed, dict) else parsed
                except Exception:
                    items = None
        if not isinstance(items, list):
            self.last_error = "scenario_parse_failed: model output had no usable scenarios list"
            return
        for it in items[:need]:
            sc = str(it or "").strip()
            if not (_MIN_LEN <= len(sc) <= _MAX_LEN):
                continue
            key = _norm_key(sc)
            if not key or key in self._seen:
                continue          # dup semantic scenario — rad
            self._seen.add(key)
            self._queue.append((self.langs[len(self._queue) % len(self.langs)], sc))

    # ─────────────── bosqich 2: to'g'ridan-to'g'ri javob + QC ───────────────
    async def next_sample(self) -> Optional[dict]:
        """Keyingi QC'dan o'tgan sample — yoki None (omadsizlik halol, sabab last_error)."""
        if len(self._queue) < 2:
            self.last_error = None  # yangi urinish — eski sababni tozalash
            await self._refill_scenarios(6)
        if not self._queue:
            self.last_error = self.last_error or "no_scenarios: filter/dedup rejected all candidates"
            return None
        lang, scenario = self._queue.pop(0)
        messages: list[dict] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": scenario})
        try:
            res = await self._model_chat(messages, max_tokens=700)
        except Exception as e:  # arxitekturik xato YASHIRILMAYDI — sabab last_error'da
            self.last_error = f"run_exception: {str(e)[:160]}"
            return None
        return self._finish(lang, scenario, res)

    def _finish(self, lang: str, scenario: str, res) -> Optional[dict]:
        self.last_error = None
        if not isinstance(res, dict) or not res.get("ok"):
            self.last_error = f"qc:model_failed: {str((res or {}).get('error') or 'unknown')[:120]}"
            return None
        answer = str(res.get("reply") or "").strip()
        if not answer:                                             # javob bo'sh
            self.last_error = "qc:empty_response: model returned no text"
            return None
        low = answer.lower()
        banned_hit = next((b for b in _QC_BANNED if b in low), None)
        if banned_hit:                                             # qoldiq diagnostika/template
            self.last_error = f"qc:banned_string:{banned_hit}"
            return None
        meta = {
            "source": "llm_direct",                # honest origin: direct model call
            "language": lang,
            "provider": res.get("provider"),
            "topic": self.topic,
        }
        return {"messages": [{"role": "user", "content": scenario},
                             {"role": "assistant", "content": answer}],
                "meta": meta}


async def generate_samples(uid: str, topic: str, size: int, languages,
                           system_prompt: str = "") -> list[dict]:
    """convenience: ketma-ket next_sample; omadsiz sample'lar tashlub yuboriladi."""
    f = Factory(uid, topic, size, languages, system_prompt)
    out = []
    for _ in range(max(size * 2, size + 4)):   # QC rad etishlari uchun zaxira
        if len(out) >= size:
            break
        s = await f.next_sample()
        if s:
            out.append(s)
    return out
