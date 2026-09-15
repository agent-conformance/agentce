"""Runnable MCP-server example (SPEC 13.4 AX-4): an MCP server emitting tool and resource evidence."""

from __future__ import annotations

import agentce_emit
from scenario import emit_mcp_server


def main() -> None:
    emit_mcp_server(agentce_emit.auto())


if __name__ == "__main__":
    main()
