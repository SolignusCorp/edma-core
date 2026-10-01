"""ARXITEKTURA IZOLYATSIYA GATE — BENCH paketi uchun (§1/§6/§15):

- evaluator/auditor/metrics/schemas/predicates/world/report/cases — PURE:
  stdlib + edma_core.bench.* ichki import; edma_core CORE modullarini
  VA bench.adapters'ni KO'RMAYDI (evaluator EDMA'dan mustaqil).
- edma_core CORE importlari FAQAT bench/adapters/edma_binding.py'da
  (execution-artifact ko'prigi).
- runner/control — bench ichki + adapters (EDMA runtime'ni BEVOSITA ko'rmaydi).
- CORE (src/edma_core, bench'dan tashqari) bench'ni import QILMAYDI
  (BENCHMARK != EDMA — bog'liqlik yo'nalishi bir tomonlama).

AST-level tekshiruv — kod o'zgarsa ham gate ishlaydi.
"""
import ast
import os

import pytest

BENCH_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "src", "edma_core", "bench")
CORE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "src", "edma_core")
BINDING = os.path.join(BENCH_DIR, "adapters", "edma_binding.py")

# edma_core core modullarini FAQAT binding ko'rishi mumkin
CORE_ONLY_FILE = BINDING

# PURE fayllar/papkalar: edma_core.* core + adapters import TAQIQLANGAN
PURE_FILES = ("evaluator.py", "auditor.py", "metrics.py", "schemas.py",
              "predicates.py", "world.py", "report.py")
PURE_DIRS = ("cases",)


def _bench_py():
    for root, _dirs, files in os.walk(BENCH_DIR):
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(root, fn)


def _imported(path):
    """(absolute_modul_nomi, level) ro'yxati."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.append((a.name, 0))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            out.append((mod, node.level))
    return out


def test_edma_core_imports_only_in_binding():
    for path in _bench_py():
        if os.path.abspath(path) == os.path.abspath(CORE_ONLY_FILE):
            continue
        for mod, _lvl in _imported(path):
            assert not (mod == "edma_core" or mod.startswith("edma_core.")) or \
                mod.startswith("edma_core.bench"), \
                f"{path}: core import faqat edma_binding'da: {mod}"


def test_binding_uses_edma_core():
    tops = {m.split(".")[0] for m, _ in _imported(BINDING)}
    assert "edma_core" in tops


@pytest.mark.parametrize("rel", PURE_FILES)
def test_pure_file_no_core_no_adapters(rel):
    p = os.path.join(BENCH_DIR, rel)
    for mod, _lvl in _imported(p):
        assert not (mod == "edma_core" or (mod.startswith("edma_core.")
                                           and not mod.startswith("edma_core.bench"))), \
            f"{rel}: PURE fayl core import qilmaydi: {mod}"
        assert not mod.startswith("edma_core.bench.adapters"), \
            f"{rel}: PURE fayl adapters import qilmaydi: {mod}"


@pytest.mark.parametrize("sub", PURE_DIRS)
def test_pure_dir_no_core_no_adapters(sub):
    d = os.path.join(BENCH_DIR, sub)
    for root, _dirs, files in os.walk(d):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(root, fn)
            for mod, _lvl in _imported(p):
                assert not (mod == "edma_core" or (mod.startswith("edma_core.")
                                                   and not mod.startswith("edma_core.bench"))), \
                    f"{p}: PURE core import qilmaydi: {mod}"
                assert not mod.startswith("edma_core.bench.adapters"), \
                    f"{p}: PURE adapters import qilmaydi: {mod}"


def test_runner_does_not_import_core_runtime():
    p = os.path.join(BENCH_DIR, "runner.py")
    for mod, _lvl in _imported(p):
        bad = mod in ("edma_core.runtime", "edma_core.actions", "edma_core.verification")
        assert not bad, f"runner.py core runtime'ni bevosita import qilmaydi: {mod}"


def test_core_does_not_import_bench():
    """BENCHMARK != EDMA: core bench'ga bog'liq EMAS (bir tomonlama chegara)."""
    for root, _dirs, files in os.walk(CORE_DIR):
        if os.path.relpath(root, CORE_DIR).split(os.sep)[0] == "bench":
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(root, fn)
            for mod, _lvl in _imported(p):
                assert not mod.startswith("edma_core.bench"), \
                    f"{p}: CORE bench'ni import qilmaydi: {mod}"
