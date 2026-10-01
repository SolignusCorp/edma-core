"""§2 CONTROL execution — Foundation Model + same instructions/context/tools/task.
Tool-loop mavjud (model JSON action taklif etsa — BIR XIL ToolExecutor bilan
bajariladi; hech qanday orchestration/verification DOP yo'q)."""
from __future__ import annotations

import json
import time

DEFAULT_SYSTEM_PROMPT = (
    "You are a capable assistant. Answer the user's task directly, honestly and "
    "concisely. If the task is ambiguous, ask ONE clarifying question instead of "
    "guessing. If tools are listed below, you may propose an action by replying with "
    "ONLY the JSON object {\"propose_action\": {\"type\": \"<tool name EXACTLY as listed>\", "
    "\"args\": {...}}} with NO prose around it. After a TOOL RESULT message you MUST give "
    "the final natural-language answer. Without tools, answer in natural language.")


def _system_prompt_for(case: dict) -> str:
    """Tool ro'yxati DETERMINISTIK ravishda system prompt'ga kiritiladi (toolset
    canonical_input'ning bir qismi)."""
    tools = sorted(case.get("available_tools") or [])
    if not tools:
        return DEFAULT_SYSTEM_PROMPT
    return (DEFAULT_SYSTEM_PROMPT + "\n\nAVAILABLE TOOLS:\n"
            + "\n".join(f"- {t}" for t in tools)
            + "\nUse ONLY these exact tool names in propose_action.")


def parse_action(text: str):
    """Prose bilan aralashgan javobdan ham BIRINCHI propose_action obyektini
    oladi (balanslangan JSON skan) — yo'q bo'lsa None."""
    from edma_core.bench.adapters.edma_binding import extract_json
    raw = str(text or "")
    data = extract_json(raw)
    if data is None:
        return None
    if isinstance(data.get("propose_action"), dict):
        pa = data["propose_action"]
        if isinstance(pa.get("type"), str):
            return {"type": pa["type"], "args": pa.get("args") or {}}
    return None


def run_control(case: dict, adapter, tool_executor, system_prompt: str = None,
                max_tool_loops: int = 3, temperature: float = 0.2) -> dict:
    """Returns CONTROL artifact (dict)."""
    sys_prompt = system_prompt or _system_prompt_for(case)
    msgs = [{"role": "system", "content": sys_prompt}]
    for m in (case.get("conversation_context") or []):
        msgs.append({"role": m.get("role"), "content": str(m.get("content") or "")})
    msgs.append({"role": "user", "content": case["input"]})

    t0 = time.perf_counter()
    text, usage_in, usage_out, calls, errors = "", 0, 0, 0, 0
    for _ in range(max_tool_loops + 1):
        res = adapter.generate(msgs, temperature=temperature,
                               max_tokens=int(case.get("max_model_calls") and 1024 or 1024))
        calls += 1
        usage_in += int((res.usage or {}).get("input_tokens") or 0)
        usage_out += int((res.usage or {}).get("output_tokens") or 0)
        text = res.text or ""
        act = parse_action(text)
        if act and act["type"] in set(case.get("available_tools") or []) and _ < max_tool_loops:
            tr = tool_executor.execute(act["type"], act.get("args") or {})
            msgs.append({"role": "assistant", "content": text})
            msgs.append({"role": "user", "content": "TOOL RESULT (" + act["type"] + "): "
                         + json.dumps(tr, ensure_ascii=False, default=str)[:1500]})
            continue
        break
    latency = (time.perf_counter() - t0) * 1000
    if not text.strip():
        errors += 1
    return {
        "mode": "control",
        "final_text": text,
        "model_calls": calls,
        "tokens": {"input": usage_in, "output": usage_out},
        "latency_ms": round(latency, 1),
        "error_count": errors,
        "action_records": tool_executor.records(),
        "verification": None,               # control'da verification DOP yo'q
        "clarification_question": "",
        "approval_requested": False,
        "authorization": None,
        "op_order": [],
        "trace_events": [],
        "escalation": None,
        "tool_capable": bool(case.get("available_tools")),
        "system_prompt": sys_prompt,
    }
