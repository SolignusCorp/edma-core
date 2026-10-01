"""§MCP testlari — stdio JSON-RPC server (Model Context Protocol) + EDMA
verification / action-authorization tools.

XAVFSIZLIK INVARIANTLARI (pin):
- MCP server hech qachon action IJRO ETMAYDI — faqat qaror (authorize),
  dalil bahosi (verify) va introspektsiya (list/dop graph).
- edma_authorize "executed": false — har javobda.
- protocol: newline-delimited JSON-RPC 2.0 (2025-06-18); stdout faqat MCP.
"""
import json
import os
import subprocess
import sys

from edma_core import actions as A
from edma_core import dop
from edma_core.mcp.server import LATEST_PROTOCOL, SUPPORTED_PROTOCOLS, MCPServer
from edma_core.mcp.tools import TOOL_NAMES, build_edma_tools

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))


def _req(id, method, params=None):
    m = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        m["params"] = params
    return m


def _init_params(version="2025-06-18"):
    return {"protocolVersion": version, "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0.0.1"}}


def _srv():
    return MCPServer()


def _call_text(server, name, arguments, id=99):
    r = server.handle(_req(id, "tools/call", {"name": name, "arguments": arguments}))
    assert r is not None and "result" in r, r
    return json.loads(r["result"]["content"][0]["text"])


# ── initialize / handshake ──

def test_initialize_returns_protocol_and_server_info():
    r = _srv().handle(_req(1, "initialize", _init_params()))
    res = r["result"]
    assert res["protocolVersion"] == "2025-06-18"
    assert "tools" in res["capabilities"]
    assert res["serverInfo"]["name"] == "edma-core"
    assert res["serverInfo"]["version"] == "0.1.0"


def test_version_negotiation_supported_echoed():
    for v in ("2025-03-26", "2024-11-05"):
        r = _srv().handle(_req(1, "initialize", _init_params(v)))
        assert r["result"]["protocolVersion"] == v


def test_version_negotiation_unknown_falls_back_to_latest():
    r = _srv().handle(_req(1, "initialize", _init_params("1999-01-01")))
    assert r["result"]["protocolVersion"] == LATEST_PROTOCOL
    assert LATEST_PROTOCOL in SUPPORTED_PROTOCOLS


def test_initialized_notification_yields_no_response():
    assert _srv().handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


# ── tools/list ──

def test_tools_list_four_tools_with_schema():
    r = _srv().handle(_req(2, "tools/list"))
    tools = r["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {"edma_authorize", "edma_verify", "edma_list_actions", "edma_dop_graph"}
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object"
        assert "properties" in t["inputSchema"]


def test_tool_names_constant_matches_registry():
    assert {t.name for t in build_edma_tools()} == set(TOOL_NAMES)


# ── edma_authorize: qaror faqat, IJRO YO'Q ──

def test_authorize_unregistered_action_default_deny():
    out = _call_text(_srv(), "edma_authorize", {"action_type": "rm.rf"})
    assert out["status"] == "denied" and "default_deny" in out["reason"]
    assert out["executed"] is False


def test_authorize_invalid_args_denied():
    out = _call_text(_srv(), "edma_authorize",
                     {"action_type": "web.fetch", "args": {"url": "short"}})
    assert out["status"] == "denied" and "invalid_args" in out["reason"]


def test_authorize_valid_action_granted_and_never_executed():
    out = _call_text(_srv(), "edma_authorize",
                     {"action_type": "web.fetch", "args": {"url": "https://example.com/page"}})
    assert out["status"] == "granted" and out["risk"] == "medium"
    assert out["executed"] is False
    assert "NOT execute" in out["note"]


def test_authorize_high_risk_pending_without_human_approval():
    """requires_human spec: approval'siz → pending_human (model o'zi grant QILA OLMAadi)."""
    def _h(uid, args, ctx):
        return {"ok": True}
    try:
        A.ACTION_REGISTRY["danger.exec"] = A.ActionSpec(
            name="danger.exec", description="t", schema={}, risk="high",
            requires_human=True, cost_estimate=0.0, handler=_h)
        srv = _srv()
        out = _call_text(srv, "edma_authorize", {"action_type": "danger.exec"})
        assert out["status"] == "pending_human" and out["requires_human"] is True
        out2 = _call_text(srv, "edma_authorize",
                          {"action_type": "danger.exec", "approval": "granted"})
        assert out2["status"] == "granted" and out2["executed"] is False
        out3 = _call_text(srv, "edma_authorize",
                          {"action_type": "danger.exec", "approval": "denied"})
        assert out3["status"] == "denied"
    finally:
        A.ACTION_REGISTRY.pop("danger.exec", None)


# ── edma_verify: dalil bahosi (ActionResult dict, model da'vosi EMAS) ──

def test_verify_mock_provider_is_uncertain_not_pass():
    """Simulyatsiya qilingan search natijasi — tasdiqlangan dalil EMAS (§10)."""
    out = _call_text(_srv(), "edma_verify", {
        "action": "web.search",
        "result": {"ok": True, "action": "web.search",
                   "evidence": {"provider": "mock", "query": "x",
                                "results": [{"url": "https://a.b", "content": "text"}]}}})
    assert out["verdict"] == "UNCERTAIN"
    assert "simulated" in out["reason"] or "mock" in out["reason"]


def test_verify_no_results_is_fail():
    out = _call_text(_srv(), "edma_verify", {
        "action": "web.search",
        "result": {"ok": True, "evidence": {"provider": "real", "query": "x", "results": []}}})
    assert out["verdict"] == "FAIL"


def test_verify_rejects_non_dict_result():
    r = _srv().handle(_req(7, "tools/call",
                           {"name": "edma_verify",
                            "arguments": {"action": "web.search", "result": "model says yes"}}))
    assert r["result"]["isError"] is True
    assert "ActionResult" in r["result"]["content"][0]["text"]


def test_verify_unknown_action_is_uncertain_honest():
    out = _call_text(_srv(), "edma_verify",
                     {"action": "nonexistent.tool", "result": {"ok": True}})
    assert out["verdict"] == "UNCERTAIN"


# ── introspektsiya ──

def test_list_actions_default_registry():
    out = _call_text(_srv(), "edma_list_actions", {})
    names = {a["name"] for a in out["actions"]}
    assert "web.fetch" in names
    assert all("schema" in a for a in out["actions"])


def test_dop_graph_13_states_and_frozen_transitions():
    out = _call_text(_srv(), "edma_dop_graph", {})
    assert len(out["states"]) == 13
    assert out["legal_transitions"]["START"] == ["PERCEIVING"]
    assert out["legal_transitions"]["RESPONSE"] == ["END"]
    assert out["legal_transitions"]["END"] == []
    assert "sole transition authority" in out["invariant"]
    assert set(out["legal_transitions"]) == set(dop.STATES)


# ── protokol xatolari (JSON-RPC 2.0) ──

def test_ping():
    r = _srv().handle(_req(5, "ping"))
    assert r["result"] == {}


def test_unknown_method_minus_32601():
    r = _srv().handle(_req(6, "resources/read", {"uri": "x"}))
    assert r["error"]["code"] == -32601


def test_unknown_tool_minus_32602():
    r = _srv().handle(_req(8, "tools/call", {"name": "nope", "arguments": {}}))
    assert r["error"]["code"] == -32602


def test_missing_tool_name_minus_32602():
    r = _srv().handle(_req(9, "tools/call", {}))
    assert r["error"]["code"] == -32602


def test_invalid_request_shape_minus_32600():
    r = _srv().handle({"jsonrpc": "1.0", "id": 10, "method": "initialize"})
    assert r["error"]["code"] == -32600
    assert _srv().handle("not a dict")["error"]["code"] == -32600


def test_handler_exception_is_isError_not_internal_leak():
    """Tool ichida xato — protokol xatosi EMAS: isError:true, stack leak YO'Q."""
    r = _srv().handle(_req(11, "tools/call",
                           {"name": "edma_authorize", "arguments": ["not-an-object"]}))
    assert "result" in r and r["result"]["isError"] is True
    assert "Traceback" not in r["result"]["content"][0]["text"]


def test_authorize_none_action_type_is_honest_deny_not_crash():
    out = _call_text(_srv(), "edma_authorize", {"action_type": None})
    assert out["status"] == "denied" and out["executed"] is False


# ── serve() loop + stdio subprocess e2e ──

def test_serve_stdout_purity_and_eof():
    import io
    srv = _srv()
    inp = io.StringIO("\n".join([
        json.dumps(_req(1, "initialize", _init_params())),
        "",  # bo'sh qator — jim o'tkaziladi
        "not json",  # parse error → -32700, server O'LMAYDI
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps(_req(2, "ping")),
    ]) + "\n")
    out = io.StringIO()
    srv.serve(inp, out)
    lines = [json.loads(l) for l in out.getvalue().strip().splitlines()]
    assert len(lines) == 3  # notification → javob YO'Q
    assert lines[0]["id"] == 1 and lines[1]["error"]["code"] == -32700
    assert lines[2]["result"] == {}


def test_stdio_subprocess_roundtrip():
    """Haqiqiy stdio transport: python -m edma_core.mcp subprocess (Claude Desktop
    xuddi shu yo'l bilan ulanadi)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC_DIR + os.pathsep + env.get("PYTHONPATH", "")
    msgs = [
        _req(1, "initialize", _init_params()),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        _req(2, "tools/list"),
        _req(3, "tools/call", {"name": "edma_authorize",
                               "arguments": {"action_type": "web.fetch",
                                             "args": {"url": "https://example.com"}}}),
        _req(4, "tools/call", {"name": "edma_dop_graph", "arguments": {}}),
    ]
    p = subprocess.run([sys.executable, "-m", "edma_core.mcp"],
                       input="\n".join(json.dumps(m) for m in msgs) + "\n",
                       capture_output=True, text=True, env=env, timeout=120, check=False)
    assert p.returncode == 0, p.stderr[-300:]
    out_lines = [json.loads(l) for l in p.stdout.strip().splitlines()]
    assert len(out_lines) == 4  # notification javobsiz
    res0 = out_lines[0]["result"]
    assert res0["protocolVersion"] == "2025-06-18"
    assert res0["serverInfo"]["name"] == "edma-core"
    assert {t["name"] for t in out_lines[1]["result"]["tools"]} == set(TOOL_NAMES)
    authz = json.loads(out_lines[2]["result"]["content"][0]["text"])
    assert authz["status"] == "granted" and authz["executed"] is False
    graph = json.loads(out_lines[3]["result"]["content"][0]["text"])
    assert len(graph["states"]) == 13
