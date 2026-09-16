"""Phase-4 P4.5 orchestrator: every control has a technique per style or an explicit N/A (SPEC §7.6).

Runs the techniques library's own coverage check in its environment. ``python techniques.py --check``
prints ``TECHNIQUES OK`` and exits 0 when the library covers every control for every framework.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_TECHNIQUES = _REPO / "spec" / "techniques"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase-4 P4.5 techniques-coverage gate."
    )
    parser.add_argument("--check", action="store_true", help="run the coverage check")
    parser.parse_args(argv)
    result = subprocess.run(
        ["uv", "run", "--project", str(_TECHNIQUES), "python", "check_techniques.py"],
        cwd=_TECHNIQUES,
        capture_output=True,
        text=True,
        check=False,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
