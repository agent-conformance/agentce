"""agentce-emit: the evidence emitter for the Agent Conformance Engine (SPEC §13.1, §13.4 AX-3).

One-line setup: ``import agentce_emit; em = agentce_emit.auto()`` returns an emitter that is
active when ``AGENTCE_EMIT=1`` and a no-op otherwise. Emitted events are canonical CloudEvents with
JSON-LD payloads, labelled ``self_report``; the bundle is written on flush (or at process exit for
``auto()``) and validates with zero quarantines. Most events are still an explicit ``emit_*`` call, but
``auto()`` alone declares a session on its own, and :func:`instrument`/:class:`AgentCESpanProcessor`
hook a framework's own OTel-shaped instrumentation where it exists (the ``otel`` extra).
"""

from __future__ import annotations

from ._emit import (
    BASE_CONTEXT,
    DEFAULT_CONVENTION,
    DEFAULT_SOURCE,
    DEFAULT_SUBJECT,
    Emitter,
    auto,
)
from ._span_processor import AgentCESpanProcessor, Span, instrument

__version__ = "0.1.0"

__all__ = [
    "BASE_CONTEXT",
    "DEFAULT_CONVENTION",
    "DEFAULT_SOURCE",
    "DEFAULT_SUBJECT",
    "AgentCESpanProcessor",
    "Emitter",
    "Span",
    "auto",
    "instrument",
    "__version__",
]
