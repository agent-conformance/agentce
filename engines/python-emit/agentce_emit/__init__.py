"""agentce-emit: the evidence emitter for the Agent Conformance Engine (SPEC §13.1, §13.4 AX-3).

One-line setup: ``import agentce_emit; em = agentce_emit.auto()`` returns an emitter that is
active when ``AGENTCE_EMIT=1`` and a no-op otherwise. Emitted events are canonical CloudEvents with
JSON-LD payloads, labelled ``self_report``; the bundle is written on flush (or at process exit for
``auto()``) and validates with zero quarantines. Every event is an explicit ``emit_*`` call: hooking an
agent framework's own instrumentation so evidence is captured from that one line alone is on the
roadmap and is not built.
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

__version__ = "0.0.1"

__all__ = [
    "BASE_CONTEXT",
    "DEFAULT_CONVENTION",
    "DEFAULT_SOURCE",
    "DEFAULT_SUBJECT",
    "Emitter",
    "auto",
    "__version__",
]
