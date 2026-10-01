"""`python -m edma_core.mcp` — stdio MCP server (Claude Desktop/Cursor config)."""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    from .server import MCPServer
    print("edma-core MCP server (stdio) — protocol "
          "2025-06-18; tools: edma_authorize/edma_verify/edma_list_actions/edma_dop_graph",
          file=sys.stderr)
    MCPServer().serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
