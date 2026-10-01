"""§14 REPRODUCIBILITY — har run metadata: hash'lar bilan."""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
import uuid

from edma_core.bench import BENCHMARK_VERSION, CASE_SET_VERSION
from edma_core.bench.schemas import case_set_hash


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=5, cwd=__import__("os").path.dirname(
                                  __path__[0]) if False else ".").stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def sha(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                     default=str).encode()).hexdigest()[:16]


def build_run_metadata(model_meta: dict, edma_meta: dict, case_set: list) -> dict:
    return {
        "run_id": "bm_" + uuid.uuid4().hex[:12],
        "timestamp": int(time.time() * 1000),
        "benchmark_version": BENCHMARK_VERSION,
        "case_set_version": CASE_SET_VERSION,
        "case_set_hash": case_set_hash(case_set),
        "model_id": model_meta.get("model_id"),
        "model_version": model_meta.get("model_version"),
        "temperature": model_meta.get("temperature", 0.2),
        "max_tokens": model_meta.get("max_tokens", 1024),
        "system_prompt_hash": model_meta.get("system_prompt_hash", ""),
        "context_hash": model_meta.get("context_hash", ""),
        "toolset_hash": model_meta.get("toolset_hash", ""),
        "edma_version": edma_meta.get("edma_version", "unknown"),
        "git_commit": _git_commit(),
    }


def build_metadata(case_set: str = "golden-v1", n: int = 0, temperature: float = 0.2,
                   adapter=None) -> dict:
    """CLI/simple runs uchun ixcham metadata (reproduksiyaga yetarli)."""
    mm = (adapter.metadata() or {}) if adapter is not None else {}
    return {
        "run_id": "bm_" + uuid.uuid4().hex[:12],
        "timestamp": int(time.time() * 1000),
        "benchmark_version": BENCHMARK_VERSION,
        "case_set_version": case_set,
        "case_count": n,
        "model_id": mm.get("model_id", "mock/scripted"),
        "model_version": mm.get("model_version", "scripted"),
        "temperature": temperature,
        "edma_version": "13-state-evidence-driven",
        "git_commit": _git_commit(),
    }
