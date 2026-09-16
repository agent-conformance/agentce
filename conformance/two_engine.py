"""The two-engine byte-identity gate (SPEC §11.5, P3.4).

Emits ``{"python": <claim>, "ts": <claim>, "projects_total": …, "projects_identical": …, …}`` for the
phase-3 eval's P3.4 check (both engines claim ``full`` and every project's ``assertions.json`` is
identical across engines). It is a thin front for :mod:`ecs`; run it as::

    cd conformance && uv run python two_engine.py --json
"""

from __future__ import annotations

import sys

from ecs import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
