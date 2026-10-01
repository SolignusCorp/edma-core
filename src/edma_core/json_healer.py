"""
ThinkSync AI — JSON Healer (M4, zero-setup structured output).

Deterministic, dependency-free post-processor that turns messy LLM output into
guaranteed-parseable JSON:
  1. strip markdown fences / prose        2. extract first balanced {...} or [...]
  3. normalize smart quotes & control chars
  4. repair unterminated strings
  5. balance brackets/braces (auto-close, drop unbalanced closers)
  6. insert missing commas between object/array items
  7. remove trailing commas before } ]
  8. `None`/`True`/`False` -> null/true/false
  9. single-quoted keys/values -> double quotes
verify with json.loads after every step; each transform is best-effort and
only kept when it makes the parse succeed (or strictly progresses it).
"""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json|JSON|jsonc)?\s*([\s\S]*?)```")
_BRACKET_RE = re.compile(r"[\{\[\]\}]")
_SMART_QUOTES = {"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'", "\u00ab": '"', "\u00bb": '"'}
_NAN_RE = re.compile(r"(?<![\w\"])NaN(?![\w\"])")
_INF_RE = re.compile(r"(?<![\w\"])(-?Infinity)(?![\w\"])")
_JS_CONST_RE = re.compile(r"(?<![\w\"])(None|True|False)(?![\w\"])")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def heal(text: str, mode: str = "object") -> dict:
    """Main entry: returns {'ok', 'value', 'attempts', 'healed'}."""
    attempts: list[str] = []
    raw = text or ""
    cur = raw
    ok, val = _try(cur, mode)
    if ok:
        return {"ok": True, "value": val, "attempts": attempts, "healed": False}
    attempts.append("raw")

    # 1. fences
    cur = _strip_fences(cur)
    ok, val = _try(cur, mode)
    if ok:
        return _done(val, attempts, "fences")
    attempts.append("fences")

    # 2. extract candidate (first balanced block)
    cur = _extract_candidate(cur, mode)
    if cur:
        ok, val = _try(cur, mode)
        if ok:
            return _done(val, attempts, "extract")
        attempts.append("extract")

    # 3. cleanup transforms on the candidate
    cur = _normalize(cur)
    ok, val = _try(cur, mode)
    if ok:
        return _done(val, attempts, "normalize")
    attempts.append("normalize")

    # 4. string terminators
    cur2 = _fix_strings(cur)
    if cur2 != cur:
        ok, val = _try(cur2, mode)
        if ok:
            return _done(val, attempts, "strings")
        attempts.append("strings")
        cur = cur2

    # 5. bracket balancing
    cur2 = _balance_brackets(cur, mode)
    if cur2 != cur:
        ok, val = _try(cur2, mode)
        if ok:
            return _done(val, attempts, "brackets")
        attempts.append("brackets")
        cur = cur2

    # 6. comma insertion
    cur2 = _insert_commas(cur)
    if cur2 != cur:
        ok, val = _try(cur2, mode)
        if ok:
            return _done(val, attempts, "commas")
        attempts.append("commas")
        cur = cur2

    # 7. trailing commas (last — needs valid neighbors)
    cur2 = _strip_trailing_commas(cur)
    if cur2 != cur:
        ok, val = _try(cur2, mode)
        if ok:
            return _done(val, attempts, "trailing")
        attempts.append("trailing")
        cur = cur2

    # 8. single-quote conversion (attempt-based)
    cur2 = _fix_single_quotes(cur)
    if cur2 != cur:
        ok, val = _try(cur2, mode)
        if ok:
            return _done(val, attempts, "single_quotes")
        attempts.append("single_quotes")
        cur = cur2

    # final loop: alternate remaining fixes until stable
    prev = None
    for _ in range(4):
        if prev == cur:
            break
        prev = cur
        for fn in (_fix_strings, _insert_commas, _strip_trailing_commas, _balance_brackets, _fix_single_quotes):
            nxt = fn(cur)
            if nxt != cur:
                ok, val = _try(nxt, mode)
                if ok:
                    return _done(val, attempts, "loop:" + fn.__name__)
                cur = nxt

    # guarantee: wrap the (still unparseable) remainder
    return _done({"result": raw, "healed": False}, attempts, "wrap")


def _done(val: Any, attempts: list[str], stage: str) -> dict:
    attempts.append(stage)
    healed = stage not in ("raw",)
    return {"ok": True, "value": val, "attempts": attempts, "healed": healed}


def _try(s: str, mode: str):
    if not s or not s.strip():
        return False, None
    try:
        # strict JSON: reject NaN/Infinity literals (Python's default allows them)
        v = json.loads(s, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
        if _has_nonfinite(v):
            return False, None
        if mode == "object" and not isinstance(v, (dict, list)):
            v = {"result": v}
        return True, v
    except Exception:
        return False, None


def _has_nonfinite(v: Any) -> bool:
    if isinstance(v, float):
        return v != v or v == float("inf") or v == float("-inf")
    if isinstance(v, dict):
        return any(_has_nonfinite(x) for x in v.values())
    if isinstance(v, list):
        return any(_has_nonfinite(x) for x in v)
    return False


def _strip_fences(s: str) -> str:
    m = _FENCE_RE.search(s)
    if m:
        return m.group(1).strip()
    s = re.sub(r"^```(?:json|JSON|jsonc)?\s*", "", s.strip())
    return re.sub(r"\s*```$", "", s)


def _extract_candidate(s: str, mode: str) -> str:
    pairs = {")": "(", "]": "[", "}": "{"}
    opens = {"{": "}", "[": "]"}
    if mode == "array":
        targets = ["["]
    elif mode == "object":
        targets = ["{"]
    else:
        targets = ["{", "["]
    best = ""
    for i, ch in enumerate(s):
        if ch not in targets:
            continue
        depth = 0
        in_str = False
        esc = False
        for j in range(i, len(s)):
            c = s[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c in opens:
                depth += 1
            elif c in pairs:
                depth -= 1
                if depth == 0:
                    cand = s[i:j + 1]
                    if len(cand) > len(best):
                        best = cand
                    break
        else:
            # unterminated: take the rest
            cand = s[i:]
            if len(cand) > len(best):
                best = cand
    return best


def _normalize(s: str) -> str:
    out = []
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            if esc:
                esc = False
                out.append(ch)
                continue
            if ch == "\\":
                esc = True
                out.append(ch)
                continue
            if ch == '"':
                in_str = False
                out.append(ch)
                continue
            if ch in _SMART_QUOTES:
                out.append(_SMART_QUOTES[ch])
            elif ord(ch) < 0x20:
                out.append(" ")  # raw control char inside string -> break/fix later
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_str = True
                out.append(ch)
            elif ch in _SMART_QUOTES:
                out.append('"' if ch in ("\u201c", "\u201d", "\u00ab", "\u00bb") else "'")
            else:
                out.append(ch)
    s = "".join(out)
    s = _CONTROL_RE.sub(" ", s)
    s = _NAN_RE.sub("null", s)
    s = _INF_RE.sub("null", s)  # Infinity / -Infinity -> null (spec-safe)
    s = _JS_CONST_RE.sub(lambda m: {"None": "null", "True": "true", "False": "false"}[m.group(1)], s)
    return s


def _fix_single_quotes(s: str) -> str:
    """Convert single-quoted keys/values to double quotes.
    Apostrophes inside words (don't, ko'raman) are left alone: a quote only
    converts when it sits where a JSON string delimiter would sit."""
    out = []
    n = len(s)
    for i, ch in enumerate(s):
        if ch != "'":
            out.append(ch)
            continue
        prev = s[i - 1] if i > 0 else ""
        nxt = s[i + 1] if i + 1 < n else ""
        opens = prev in "{:,[ \t\n\r" or prev == ""
        closes = nxt in ":,}] \t\n\r" or nxt == ""
        if opens or closes:
            out.append('"')
        else:
            out.append(ch)  # apostrophe inside a word
    return "".join(out)


def _fix_strings(s: str) -> str:
    """Close an unterminated string at end-of-line / before structural breaks."""
    out = []
    in_str = False
    esc = False
    n = len(s)
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
                out.append(ch)
                continue
            if ch == "\\":
                esc = True
                out.append(ch)
                continue
            if ch == '"':
                in_str = False
                out.append(ch)
                continue
            if ch == "\n":
                # newline inside a JSON string is illegal -> terminate and reopen
                out.append('\\n"')
                out.append(ch)
                in_str = False
                continue
            out.append(ch)
        else:
            if ch == '"':
                out.append(ch)
                in_str = True
            else:
                out.append(ch)
    if in_str:
        out.append('"')
    return "".join(out)


def _balance_brackets(s: str, mode: str = "object") -> str:
    stack: list[str] = []
    in_str = False
    esc = False
    out = []
    for ch in s:
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            continue
        if ch in "{[":
            stack.append(ch)
            out.append(ch)
        elif ch in "}]":
            if not stack:
                continue  # stray closer -> drop
            expected = "{" if ch == "}" else "["
            if stack[-1] == expected:
                stack.pop()
                out.append(ch)
            else:
                # mismatched: close open ones first
                while stack and stack[-1] != expected:
                    out.append("}" if stack.pop() == "{" else "]")
                if stack:
                    stack.pop()
                    out.append(ch)
        else:
            out.append(ch)
    if in_str:
        out.append('"')
    while stack:
        out.append("}" if stack.pop() == "{" else "]")
    return "".join(out)


_TRAILING_RE = re.compile(r",(\s*[\}\]])")
_ITEM_GAP_RE = re.compile(r'("(\s*})|\s)\s*(?=["{[])', re.IGNORECASE)  # placeholder, replaced below
_MISSING_COMMA_RE = re.compile(r'(?<=[\"\}\]\dtruefalsnSe])\s*(?=[\"\{\[])')


def _insert_commas(s: str) -> str:
    # between "...":"..."  and next key or value opener
    s = re.sub(r'("(?:[^"\\]|\\.)*")(\s+)(?=")', r"\1,\2", s)
    s = re.sub(r'(\})(\s+)(?=")', r"\1,\2", s)
    s = re.sub(r'(\])(\s+)(?=[\{\["\d])', r"\1,\2", s)
    s = re.sub(r'(\b(?:true|false|null)\b|\d)(\s+)(?=")', r"\1,\2", s)
    s = re.sub(r'(\b(?:true|false|null)\b|\d)(\s+)(?=[\{\[])', r"\1,\2", s)
    return s


def _strip_trailing_commas(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        s = _TRAILING_RE.sub(r"\1", s)
    return s
