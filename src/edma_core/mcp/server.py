"""§MCP SERVER — Model Context Protocol (stdio transport) bo'yicha EDMA server.

Spec: newline-delimited JSON-RPC 2.0 over stdin/stdout (UTF-8, javob satrlari
faqat stdout'ga; log'lar stderr'ga). Qo'llab-quvvatlanadigan protokol:
2025-06-18 (latest), 2025-03-26, 2024-11-05.

Sodda stdlib implementatsiya (zero-dependency core tamoyili) — rasmiy `mcp`
SDK'siz ham Claude Desktop / Cursor ulana oladi.
"""
from __future__ import annotations

import json
import sys
from importlib import metadata

JSONRPC = "2.0"
LATEST_PROTOCOL = "2025-06-18"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


def _core_version() -> str:
    try:
        return metadata.version("edma-core")
    except Exception:
        from edma_core import __version__
        return __version__


class _MethodNotFound(Exception):
    pass


class _InvalidParams(Exception):
    pass


class MCPServer:
    """JSON-RPC dispatcher + stdio loop. `tools` bo'sh bo'lsa default EDMA
    tools yuklanadi (edma_core.mcp.tools.build_edma_tools)."""

    def __init__(self, tools=None, server_info: dict | None = None):
        if tools is None:
            from .tools import build_edma_tools
            tools = build_edma_tools()
        self.tools = {t.name: t for t in tools}
        self.server_info = server_info or {"name": "edma-core", "version": _core_version()}

    # ── JSON-RPC yordamchilari ──

    @staticmethod
    def _result(id_, result) -> dict:
        return {"jsonrpc": JSONRPC, "id": id_, "result": result}

    @staticmethod
    def _error(id_, code: int, message: str) -> dict:
        return {"jsonrpc": JSONRPC, "id": id_, "error": {"code": code, "message": message}}

    # ── dispatch ──

    def handle(self, msg) -> dict | None:
        """Bir JSON-RPC xabarini ishlaydi. Notification → None (javob yo'q)."""
        if (not isinstance(msg, dict) or msg.get("jsonrpc") != JSONRPC
                or not isinstance(msg.get("method"), str)):
            bad_id = msg.get("id") if isinstance(msg, dict) else None
            return self._error(bad_id, INVALID_REQUEST,
                               "invalid JSON-RPC 2.0 request (jsonrpc/method/id)")
        is_notification = "id" not in msg
        id_ = msg.get("id")
        method = msg["method"]
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            if is_notification:
                return None
            return self._error(id_, INVALID_PARAMS, "params MUST be an object")
        try:
            result = self._dispatch(method, params)
        except (_MethodNotFound, _InvalidParams) as e:
            if is_notification:  # spec: notification'larga xato ham YO'Q
                return None
            code = METHOD_NOT_FOUND if isinstance(e, _MethodNotFound) else INVALID_PARAMS
            return self._error(id_, code, str(e))
        if is_notification or result is None:
            return None
        return self._result(id_, result)

    def _dispatch(self, method: str, params: dict):
        if method == "initialize":
            requested = str(params.get("protocolVersion") or "")
            version = requested if requested in SUPPORTED_PROTOCOLS else LATEST_PROTOCOL
            return {"protocolVersion": version,
                    "capabilities": {"tools": {}},
                    "serverInfo": dict(self.server_info)}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [t.descriptor() for t in self.tools.values()]}
        if method == "tools/call":
            name = params.get("name")
            tool = self.tools.get(name) if isinstance(name, str) else None
            if tool is None:
                raise _InvalidParams(f"unknown tool: {name!r}")
            arguments = params.get("arguments") or {}
            return self._call_tool(tool, arguments)
        raise _MethodNotFound(f"method not found: {method}")

    @staticmethod
    def _call_tool(tool, arguments) -> dict:
        try:
            payload = tool.handler(arguments)
            text = json.dumps(payload, ensure_ascii=False, default=str)
            is_error = False
        except Exception as e:  # tool darajasidagi xato — protokol xatosi EMAS
            payload = {"error": f"{type(e).__name__}: {e}"[:300]}
            text = json.dumps(payload, ensure_ascii=False, default=str)
            is_error = True
        return {"content": [{"type": "text", "text": text}], "isError": is_error}

    # ── stdio transport (newline-delimited) ──

    def serve(self, inp=None, out=None) -> None:
        """Satrma-satr o'qiydi; bo'sh satrlar jim o'tadi; parse error → -32700
        (server o'lmaydi); EOF → yakun. stdout FAQAT MCP xabarlari."""
        inp = sys.stdin if inp is None else inp
        out = sys.stdout if out is None else out
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as e:
                resp = self._error(None, PARSE_ERROR, f"parse error: {e.msg}")
            else:
                resp = self.handle(msg)
            if resp is not None:
                out.write(json.dumps(resp, ensure_ascii=False, default=str) + "\n")
                out.flush()
