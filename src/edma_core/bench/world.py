"""World-set grading — dataset REFERENCE kalitlari bilan deterministik.

GSM8K (numeric, '#### <answer>') va MMLU (mc: A/B/C/D) uchun ikkita
deterministik qoida — hech qanday model bahosi EMAS. Wilson 95% CI ham
shu yerda (halol xisobot uchun; world-v1 n=30 kabi kichik to'plamlarda).
"""
from __future__ import annotations

import math
import os
import re

_DIR = os.path.dirname(os.path.abspath(__file__))
WORLD_SETS = {"world-v1": os.path.join(_DIR, "cases", "world_v1.json"),
              "world-v2": os.path.join(_DIR, "cases", "world_v2_multidomain.json")}


def wilson(p: float, n: int, z: float = 1.96) -> tuple:
    """Wilson score interval (95% default) — kichik n uchun halol oraliq."""
    if n == 0:
        return (0.0, 0.0)
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 3), round(c + h, 3))


def last_number(text: str):
    nums = re.findall(r"-?\d[\d,]*(?:\.\d+)?", str(text or "").replace(",", ""))
    if not nums:
        return None
    try:
        return float(nums[-1])
    except ValueError:
        return None


def grade(item: dict, text: str) -> bool:
    t = str(text or "")
    if item["grading"] == "numeric":
        m = re.search(r"####\s*(-?\d[\d,]*)", t)
        if m:
            got = m.group(1).replace(",", "")
        else:
            ln = last_number(t)
            got = None if ln is None else str(int(ln)) if ln == int(ln) else str(ln)
        ref = item["reference"].replace(",", "")
        return got is not None and float(got) == float(ref)
    letters = re.findall(r"\b([ABCD])\b", t.upper())
    return bool(letters) and letters[-1] == item["reference"]


def control_prompt(item: dict) -> list:
    q = item["question"]
    if item["grading"] == "numeric":
        sys_p = ("You are a careful math solver. Solve step by step and end your reply with "
                 "the final numeric answer on its own line formatted exactly as: #### <answer>")
        return [{"role": "system", "content": sys_p}, {"role": "user", "content": q}]
    sys_p = ("The following is a multiple choice question. Answer with ONLY the letter of the "
             "correct option (A, B, C or D) — nothing else.")
    return [{"role": "system", "content": sys_p}, {"role": "user", "content": q}]


def run_world_set(adapter, name: str = "world-v1", limit: int = 0, mode_name: str = "") -> dict:
    """Butun world set'ni adapter orqali bajarib, deterministik grade qiladi.
    Returns: {n, correct, rate, ci95, per_item:[...]} — FAQAT FAKTY."""
    w = load_world_json(name)
    items = w["items"][:limit] if limit else w["items"]
    per = []
    correct = 0
    for it in items:
        res = adapter.generate(control_prompt(it), temperature=0.0)
        ok = bool(grade(it, res.text))
        correct += int(ok)
        per.append({"id": it.get("id") or it.get("question", "")[:40],
                    "dataset": it.get("dataset"), "ok": ok,
                    "answer": str(res.text or "")[:160]})
    n = len(items)
    p = round(correct / n, 4) if n else 0.0
    return {"set": name, "mode": mode_name or getattr(adapter, "name", "adapter"),
            "n": n, "correct": correct, "rate": p,
            "ci95": list(wilson(correct / n, n)) if n else [0.0, 0.0],
            "per_item": per}


def load_world_json(name: str = "world-v1") -> dict:
    import json
    path = WORLD_SETS.get(name)
    if not path:
        raise ValueError(f"unknown world set: {name}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)
