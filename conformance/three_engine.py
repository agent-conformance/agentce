"""The three-engine byte-identity gate (SPEC §11.5, P5.1).

Emits ``{"identical": bool, "engines": ["python", "typescript", "java"], "projects": N, …}`` for the
phase-5 eval's P5.1 check: all three engines claim ``full`` and every project's canonical set
(``assertions.json``, ``oscal-ar.json``, ``results.sarif``, and every ``packs/*/pack.json``) is identical
across engines over the full corpus. It is a thin front for :mod:`ecs`; run it as::

    cd conformance && uv run python three_engine.py --json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ecs import REPO_ROOT, _all_identical, run_multi_engine

ENGINES = ["python", "typescript", "java"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="three_engine",
        description="Three-engine byte-identity gate over the full corpus (SPEC §11.5, P5.1).",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=REPO_ROOT / "corpus",
        help="the corpus directory or source tree",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args(argv)

    result = run_multi_engine(args.corpus, ENGINES)
    out = {
        "identical": _all_identical(result),
        "engines": ENGINES,
        "projects": result["projects_total"],
        "projects_identical": result["projects_identical"],
        "claims": result["claims"],
        "different": result["different"],
        "divergences": result["divergences"],
    }
    if args.json:
        print(json.dumps(out, sort_keys=True))
    else:
        print(
            f"identical={out['identical']} engines={len(ENGINES)} "
            f"projects={out['projects']} ({out['projects_identical']} identical)"
        )
    return 0 if out["identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
