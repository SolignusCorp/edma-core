"""Case set yuklovchi — cases + deterministik skriptlar (versiyalanadi)."""
from __future__ import annotations

import json
import os

_DIR = os.path.dirname(os.path.abspath(__file__))

SUITES = {
    "golden-v1": ("golden_cases.json", "golden_scripts.json"),
    "world-v1": ("world_v1.json", None),
    "world-v2": ("world_v2_multidomain.json", None),
}


def load_golden_cases() -> list:
    with open(os.path.join(_DIR, "golden_cases.json"), encoding="utf-8") as f:
        return json.load(f)["cases"]


def load_golden_scripts() -> dict:
    with open(os.path.join(_DIR, "golden_scripts.json"), encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if k != "comment"}


def scripts_for(case_id: str) -> dict:
    return load_golden_scripts().get(case_id) or {}


def load_world_set(name: str = "world-v1") -> dict:
    """World set (GSM8K/MMLU uslubi): {'set_version','items','grading'...} verbatim."""
    fn = {"world-v1": "world_v1.json", "world-v2": "world_v2_multidomain.json"}.get(name)
    if not fn:
        raise ValueError(f"unknown world set: {name}")
    with open(os.path.join(_DIR, fn), encoding="utf-8") as f:
        return json.load(f)


def suite_info() -> dict:
    out = {}
    for name, (cases_fn, _scripts) in SUITES.items():
        with open(os.path.join(_DIR, cases_fn), encoding="utf-8") as f:
            d = json.load(f)
        out[name] = {"file": cases_fn, "count": len(d.get("cases") or d.get("items") or []),
                     "version": d.get("case_set_version") or d.get("set_version"),
                     "description": d.get("description", "")}
    return out
