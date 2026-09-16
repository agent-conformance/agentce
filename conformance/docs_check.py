"""The documentation gate (SPEC §13.4, P5.3).

Emits ``{"build_clean": bool, "links_clean": bool}`` for the phase-5 eval's P5.3 check: the docs site
builds with no warning (the generated reference pages are complete and current) and the offline link
check finds no broken internal link. It is a thin front for ``docs/build.py``; run it as::

    cd conformance && uv run python docs_check.py --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD = REPO_ROOT / "docs" / "build.py"


def run_docs_check() -> dict[str, bool]:
    """Run ``docs/build.py --check-links --json`` and return its result."""
    completed = subprocess.run(
        [sys.executable, str(BUILD), "--check-links", "--json"],
        cwd=BUILD.parent,
        capture_output=True,
        text=True,
    )
    if completed.returncode not in (0, 1) or not completed.stdout.strip():
        raise RuntimeError(f"docs build failed: {completed.stderr.strip() or completed.stdout.strip()}")
    return json.loads(completed.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docs_check", description="Documentation build and link gate (P5.3)."
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    result = run_docs_check()
    out = {
        "build_clean": bool(result.get("build_clean")),
        "links_clean": bool(result.get("links_clean")),
    }
    ok = out["build_clean"] and out["links_clean"]
    if args.json:
        print(json.dumps(out, sort_keys=True))
    else:
        print(f"build_clean={out['build_clean']} links_clean={out['links_clean']}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
