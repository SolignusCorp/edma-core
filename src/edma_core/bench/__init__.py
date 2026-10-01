"""EDMA BENCH — mustaqil benchmark laboratoriyasi (core paketining o'lchov qatlami).

PRINSIP (direktiv §1/§6/§8/§15 — SaaS'dan o'zgartirilmasdan olingan):
  BENCHMARK != EDMA. Evaluator EDMA'dan MUSTAQIL — EDMA o'z natijasiga
  o'zi PASS bermaydi. EDMA faqat execution ARTIFACT beradi; evaluator
  artifact'dagi observable evidence bilan hukm qiladi.

  NO EVIDENCE -> NO PASS · MODEL CLAIM -> NOT EVIDENCE ·
  EDMA SELF-REPORT -> NOT EVIDENCE · UNCERTAIN hech qachon PASS bo'lmaydi.

Modul izolyatsiyasi (test_import_lint bilan pinlangan):
  evaluator / auditor / metrics / schemas / cases / report — PURE
  (edma_core'ni HAM import qilmaydi). Faqat bench/adapters/edma_binding.py
  EDMA runtime bilan ko'prik bo'ladi (execution artifact yig'ish).

G'olib AVTOMATIK e'lon QILINMAYDI: report faqat FAKTY + delta + Wilson CI;
failed case'lar hech qachon yashirilmaydi.

CLI: python -m edma_core.bench --help
"""
BENCHMARK_VERSION = "1.0.0-core"
CASE_SET_VERSION = "golden-v1"
