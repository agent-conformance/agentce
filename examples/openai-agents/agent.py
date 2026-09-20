"""Scripted OpenAI Agents SDK-style example (SPEC 13.4 AX-4): it emits a credit session through agentce-emit.

This script does not import or run OpenAI Agents SDK. It is a framework-free scenario that emits, with explicit
``agentce_emit`` calls, the evidence a OpenAI Agents SDK agent would produce. Capturing that evidence automatically
from OpenAI Agents SDK itself is on the roadmap and is not built: today ``agentce_emit.auto()`` only returns an
emitter, and every event below is a call this script makes.

``agentce_emit.auto()`` is active only when ``AGENTCE_EMIT=1`` (set by run.sh), so wiring it in never
changes behaviour until it is switched on. The bundle is flushed at process exit.
"""

from __future__ import annotations

import agentce_emit
from scenario import emit_framework_session


def main() -> None:
    emitter = agentce_emit.auto()
    emit_framework_session(emitter, style="openai-agents")


if __name__ == "__main__":
    main()
