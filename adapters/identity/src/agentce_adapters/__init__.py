"""AgentCE adapters: source-format to canonical evidence (SPEC 12).

This distribution provides the ``identity`` adapter (v1.1), which maps IdP issuance and token-exchange
logs to canonical DelegationIssued evidence events.
"""

from __future__ import annotations

from .identity import (
    AdapterError,
    AdapterReport,
    AdaptResult,
    SkippedRecord,
    adapt,
)

__all__ = [
    "AdaptResult",
    "AdapterError",
    "AdapterReport",
    "SkippedRecord",
    "adapt",
]
