"""Phase-4 P4.2 orchestrator: the automation coverage matrix is regenerated and equals the committed
one (SPEC §7.5).

``python coverage_matrix.py --check`` runs ``agentce catalog coverage-matrix --check`` over the base
catalog and the overlays and prints ``MATRIX OK`` when every committed matrix is current.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engines" / "python"
_CATALOGS = [
    _REPO / "spec" / "catalogs" / "base" / "eu-ai-act",
    _REPO / "spec" / "catalogs" / "overlays" / "conduct",
]


def _check(catalog_dir: Path) -> bool:
    result = subprocess.run(
        ["uv", "run", "agentce", "catalog", "coverage-matrix", str(catalog_dir), "--check"],
        cwd=_ENGINE,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase-4 P4.2 coverage-matrix gate.")
    parser.add_argument("--check", action="store_true", help="check every committed matrix")
    parser.parse_args(argv)
    ok = all(_check(catalog_dir) for catalog_dir in _CATALOGS)
    print("MATRIX OK" if ok else "MATRIX FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
