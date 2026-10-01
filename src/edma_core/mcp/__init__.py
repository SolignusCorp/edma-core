"""§MCP — Model Context Protocol server (edma_core.mcp).

Tashqi agentlar (Claude Desktop, Cursor, ...) EDMA'ning tekshiruv
(verification) va xavfsiz harakatlar (action authorization, default-deny)
qatlamidan STANDART TOOL sifatida foydalanadi:

  edma_authorize     — taklif qilingan action uchun qaror (granted|denied|
                       pending_human). IJRO YO'Q — faqat qaror.
  edma_verify        — bajarilgan action'ning ActionResult dict'i ustida
                       dalilga asoslangan verdict (NO EVIDENCE -> NO PASS).
  edma_list_actions  — ro'yxatdan o'tgan action'lar (risk/schema).
  edma_dop_graph     — muzlatilgan 13-holatli DOP grafi (introspektsiya).

Transport: stdio (newline-delimited JSON-RPC 2.0) — `python -m edma_core.mcp`
yoki `edma-mcp` konsol skripti. Zero-dependency (faqat stdlib).
"""
from .server import LATEST_PROTOCOL, SUPPORTED_PROTOCOLS, MCPServer
from .tools import TOOL_NAMES, Tool, build_edma_tools

__all__ = [
           "LATEST_PROTOCOL",
           "SUPPORTED_PROTOCOLS",
           "TOOL_NAMES",
           "MCPServer",
           "Tool",
           "build_edma_tools",
]
