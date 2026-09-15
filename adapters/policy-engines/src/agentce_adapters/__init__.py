"""AgentCE adapters: source-format to canonical evidence (SPEC 12).

This distribution provides the ``policy-engines`` adapter, which maps OpenFGA, OPA, Cedar, and
governance-toolkit decision logs to canonical AgentCE evidence events.
"""

from __future__ import annotations

from .policy_engines import (
    ENGINES,
    AdapterError,
    AdapterReport,
    AdaptResult,
    SkippedEntry,
    adapt,
)

__all__ = [
    "ENGINES",
    "AdaptResult",
    "AdapterError",
    "AdapterReport",
    "SkippedEntry",
    "adapt",
]
