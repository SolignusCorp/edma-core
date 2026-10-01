"""EDMA Operation Graph — request-specific, DOP-mustaqil, DETERMINISTIC tekshiruv.

Muhandislik direktivasi (CRITICAL-2):
  - Reasoning (Humanoid) SEMANTIK plan taklif qiladi: ops=[{id,kind,args,depends_on}].
  - Bu modul faqat DETERMINISTIK validatsiya/lifecycle: hech qanday I/O, model
    chaqiruvi, holat o'tishi YO'Q. DOP Engine yagona routing avtoriteti QOLADI —
    op-graf DOP grafidan alohida (frozen 13 state o'zgarmagan).
  - Model nazorat maydonlari (authorized/verified/state/...) op ichida ham
    TAQIQLANGAN — ular `ignored`'ga chiqadi (runtime hech qachon o'qimaydi).

Lifecycle (deterministik): PENDING → READY → RUNNING → PASS | FAIL | UNCERTAIN
(BLOCKED = dependency PASS bo'lmagan). Tarixiy bajarilgan op O'ZGARMAS (immutable):
revizyonda xuddi shu fingerprint'li PASS op yangi grafda ham PASS deb o'tadi,
semantikasi o'zgargan (fp boshqacha) op — YANGI op sifatida qayta bajariladi.
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

OP_STATES = ("PENDING", "READY", "RUNNING", "PASS", "FAIL", "UNCERTAIN", "BLOCKED")

# Model op ichida o'zgartira olmaydigan nazorat kalitlari (scrub bilan bir xil ruh)
BANNED_OP_KEYS = {"authorized", "approved", "verified", "verification_passed", "success",
                  "state", "next_state", "dop", "goto", "transition", "permission",
                  "grant", "authorize", "status"}

MAX_OPS_PER_PLAN = 6          # §12: bir plandagi maksimal operatsiyalar
MAX_GRAPH_VERSIONS = 3        # §12: maksimal graf versiyalari (re-planning bound)


def plan_fingerprint(op: dict) -> str:
    """Op'ning semantik barmoq izi: (id, kind, canonical args). Deterministik."""
    canonical = json.dumps({"id": op.get("id"), "kind": op.get("kind"),
                            "args": op.get("args") or {}},
                           default=str, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(canonical.encode()).hexdigest()[:16]


def _scrub_op(raw: dict) -> tuple[dict, dict]:
    """Bir op'dan nazorat kalitlarini ajratib olish → (clean, ignored)."""
    ignored = {k: raw[k] for k in list(raw.keys()) if str(k).lower() in BANNED_OP_KEYS}
    clean = {k: v for k, v in raw.items() if k not in ignored}
    return clean, ignored


def _has_cycle(ops: list[dict]) -> bool:
    """Kahn topological sort: barcha op tartiblanmasa — cycle bor."""
    indeg = {o["id"]: 0 for o in ops}
    adj: dict[str, list[str]] = {o["id"]: [] for o in ops}
    for o in ops:
        for d in o["depends_on"]:
            adj[d].append(o["id"])
            indeg[o["id"]] += 1
    queue = [i for i, n in indeg.items() if n == 0]
    seen = 0
    while queue:
        n = queue.pop()
        seen += 1
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    return seen != len(ops)


def validate_plan(raw_ops) -> tuple[Optional[list[dict]], Optional[str], dict]:
    """Xom ops ro'yxatini deterministik tekshirish.

    → (normalized_ops | None, error | None, ignored_control_fields).
    Rad etiladigan: bo'sh/no'to'g'ri tuzilma, bo'sh id, takroriy id, o'ziga
    bog'liqlik, noma'lum dependency, cycle, noma'lum kind shakli, malformed args.
    """
    ignored: dict = {}
    if not isinstance(raw_ops, list) or not raw_ops:
        return None, "ops_not_nonempty_list", ignored
    if len(raw_ops) > MAX_OPS_PER_PLAN:
        return None, f"too_many_ops (max {MAX_OPS_PER_PLAN})", ignored
    seen_ids: set[str] = set()
    ops: list[dict] = []
    for i, raw in enumerate(raw_ops):
        if not isinstance(raw, dict):
            return None, f"op_{i}_not_object", ignored
        raw, ig = _scrub_op(raw)
        for k, v in ig.items():
            ignored[f"op.{raw.get('id') or i}.{k}"] = v
        oid = raw.get("id")
        if not isinstance(oid, str) or not oid.strip():
            return None, f"op_{i}_empty_id", ignored
        oid = oid.strip()[:40]
        if oid in seen_ids:
            return None, f"duplicate_op_id:{oid}", ignored
        seen_ids.add(oid)
        kind = raw.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            return None, f"op_{oid}_empty_kind", ignored
        args = raw.get("args")
        if args is not None and not isinstance(args, dict):
            return None, f"op_{oid}_args_not_object", ignored
        deps = raw.get("depends_on", [])
        if deps is None:
            deps = []
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            return None, f"op_{oid}_depends_on_not_list", ignored
        deps = [d.strip()[:40] for d in deps if str(d).strip()]
        if oid in deps:
            return None, f"op_{oid}_self_dependency", ignored
        ops.append({"id": oid, "kind": kind.strip()[:40], "args": args or {},
                    "depends_on": deps})
    for o in ops:
        for d in o["depends_on"]:
            if d not in seen_ids:
                return None, f"op_{o['id']}_unknown_dependency:{d}", ignored
    if _has_cycle(ops):
        return None, "dependency_cycle", ignored
    return ops, None, ignored


def initial_statuses(ops: list[dict], ledger: dict) -> dict:
    """Graf statuslari: tarixiy PASS (fp mos) saqlanadi — boshqasi PENDING.

    ledger: {op_id: {status, fp, version, ...}} — bajarilgan op'lar tarixi
    (immutable). Semantikasi o'zgargan op (fp boshqacha) QAYTA PENDING bo'ladi;
    eski natija ledger'da o'zgarmay qoladi — lekin yangi natija esa
    `_record_result` orqali tarixga YANGI yozuv bo'lib qo'shiladi.
    """
    st: dict[str, str] = {}
    for o in ops:
        hist = ledger.get(o["id"]) or {}
        st[o["id"]] = "PASS" if (hist.get("status") == "PASS"
                                 and hist.get("fp") == plan_fingerprint(o)) else "PENDING"
    return st


def ready_ops(ops: list[dict], statuses: dict) -> list[str]:
    """Bajarilishga tayyor op id'lari (PENDING, barcha dep PASS) — tartib saqlanadi."""
    by_id = {o["id"]: o for o in ops}
    out = []
    for o in ops:
        if statuses.get(o["id"]) != "PENDING":
            continue
        if all(statuses.get(d) == "PASS" for d in by_id[o["id"]]["depends_on"]):
            out.append(o["id"])
    return out


def plan_summary(ops: list[dict], statuses: dict) -> dict:
    """Safe metadata (trace/RESPONSE konteksti uchun) — args matni KIRMAYDI."""
    return {"ops": [{"id": o["id"], "kind": o["kind"],
                     "depends_on": o["depends_on"],
                     "status": statuses.get(o["id"], "PENDING")} for o in ops]}


def pending_left(ops: list[dict], statuses: dict) -> bool:
    return any(statuses.get(o["id"]) not in ("PASS",) for o in ops)


def validate_revision(raw_ops, ledger: dict) -> tuple[Optional[list[dict]], Optional[str], dict]:
    """Revizyon validatsiyasi — hozircha validate_plan bilan bir xil shartnoma;
    tarixiy immutabillik initial_statuses/ledger orqali ta'minlanadi
    (runtime PASS fp o'xshashini ko'taradi, ledger esa o'zgarmas yozuv)."""
    return validate_plan(raw_ops)
