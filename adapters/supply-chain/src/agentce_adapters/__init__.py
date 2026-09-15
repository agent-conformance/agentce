"""AgentCE adapters: source-format to canonical evidence (SPEC 12).

This distribution provides the ``supply-chain`` adapter, which maps Sigstore/in-toto attestations,
CycloneDX AIBOMs, and bundle-load logs to canonical BundleLoaded and Attestation evidence events.
"""

from __future__ import annotations

from .supply_chain import (
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
