"""Runnable openai-agents example (SPEC 13.4 AX-4): one line wires agentce-emit; it emits a credit session.

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
