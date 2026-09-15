"""AgentCE adapters: source-format to canonical evidence (SPEC 12).

This distribution provides the ``otel-genai`` adapter, which maps OpenTelemetry GenAI and
OpenInference trace exports to canonical AgentCE evidence events.
"""

from __future__ import annotations

from .otel_genai import (
    AdapterError,
    AdapterReport,
    AdaptResult,
    SkippedSpan,
    adapt,
)

__all__ = [
    "AdaptResult",
    "AdapterError",
    "AdapterReport",
    "SkippedSpan",
    "adapt",
]
