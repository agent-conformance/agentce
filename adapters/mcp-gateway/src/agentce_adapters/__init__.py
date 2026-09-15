"""AgentCE adapters: source-format to canonical evidence (SPEC 12).

This distribution provides the ``mcp-gateway`` adapter, which maps MCP gateway request/response logs
to canonical AgentCE evidence events and a source manifest for coverage.
"""

from __future__ import annotations

from .mcp_gateway import (
    AdapterError,
    AdapterReport,
    AdaptResult,
    SkippedEntry,
    adapt,
)

__all__ = [
    "AdaptResult",
    "AdapterError",
    "AdapterReport",
    "SkippedEntry",
    "adapt",
]
