"""Runnable A2A-mesh example (SPEC 13.4 AX-4): a two-agent mesh emitting correlated evidence."""

from __future__ import annotations

import agentce_emit
from scenario import emit_a2a_mesh


def main() -> None:
    emit_a2a_mesh(agentce_emit.auto())


if __name__ == "__main__":
    main()
