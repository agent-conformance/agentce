"""Scripted custom-loop example (SPEC 13.4 AX-4): it emits a credit session through agentce-emit.

A hand-rolled agent loop has no agent framework to import, so this script emits its evidence with explicit
``agentce_emit`` calls. Capturing evidence automatically from a model SDK's OpenTelemetry instrumentation
is on the roadmap and is not built: today ``agentce_emit.auto()`` only returns an emitter, and every event
below is a call this script makes.

``agentce_emit.auto()`` is active only when ``AGENTCE_EMIT=1`` (set by run.sh), so wiring it in never
changes behaviour until it is switched on. The bundle is flushed at process exit.
"""

from __future__ import annotations

import agentce_emit
from scenario import emit_framework_session


def main() -> None:
    emitter = agentce_emit.auto()
    emit_framework_session(emitter, style="custom-loop")


if __name__ == "__main__":
    main()
