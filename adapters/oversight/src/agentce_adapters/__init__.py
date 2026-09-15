"""AgentCE adapters: source-format to canonical evidence (SPEC 12).

This distribution provides the ``oversight`` adapter, which maps approval-gate, workflow-engine, and
ticketing exports to canonical human-oversight evidence events.
"""

from __future__ import annotations

from .oversight import (
    SOURCE_FORMATS,
    AdapterError,
    AdapterReport,
    AdaptResult,
    SkippedRecord,
    adapt,
)

__all__ = [
    "SOURCE_FORMATS",
    "AdaptResult",
    "AdapterError",
    "AdapterReport",
    "SkippedRecord",
    "adapt",
]
