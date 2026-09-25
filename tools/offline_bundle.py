"""Air-gap bundle gate, from the tools side (item 16.4, P16.7).

``phases/phase-16/eval.sh`` pins ``uv run --project tools --frozen python -m offline_bundle --verify
--no-network`` — a check that must run from the ``tools`` package's own environment, which imports
neither ``agentce`` nor ``release``/``signing``. The real build/verify logic lives in
``conformance/offline_bundle.py`` and must run inside ``conformance``'s own environment, where the
``agentce`` console script the inner verify step shells out to is actually installed.

So this module is a thin subprocess wrapper: ``--verify`` is a flag (no directory argument) that
builds a fresh bundle to a temp directory and verifies it offline, by shelling out to
``conformance/offline_bundle.py --build`` then ``--verify [--no-network]`` inside ``conformance``'s
environment, and cleans the temp directory up afterward. Both the inner ``--project`` argument and the
inner script path must be absolute: ``--project`` alone only selects the environment `uv` runs the
interpreter in, while the interpreter's own argv[0] (the script path) is still resolved against the
subprocess's ``cwd`` — so a relative script path with ``cwd=repo_root`` fails to open the file, and
`conformance/offline_bundle.py`'s own ``REPO_ROOT = Path(__file__).resolve().parents[1]`` (and its
``sys.path[0]``, needed to import ``release`` and ``agentce.signing``) both follow the *script path* it
was invoked with, not ``cwd`` — matching ``tools/catalog_authoring_check.py::run_real_lint``'s real-lint
shell-out precedent (``cwd=repo_root`` together with an absolute ``--project``).

Run it as::

    uv run --project tools --frozen python -m offline_bundle --verify --no-network
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE_DIR = REPO_ROOT / "conformance"
CONFORMANCE_OFFLINE_BUNDLE = CONFORMANCE_DIR / "offline_bundle.py"


def _run_inner(*args: str) -> subprocess.CompletedProcess[str]:
    """Run one ``conformance/offline_bundle.py`` subcommand inside ``conformance``'s own environment."""
    return subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(CONFORMANCE_DIR),
            "--frozen",
            "python",
            str(CONFORMANCE_OFFLINE_BUNDLE),
            *args,
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )


def verify(*, no_network: bool) -> int:
    """Build a fresh bundle to a temp dir, verify it offline, and clean up."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="agentce-offline-bundle-"))
    try:
        build = _run_inner("--build", "--out", str(tmp_dir))
        if build.returncode != 0:
            print("OFFLINE BUNDLE FAILED (build):", file=sys.stderr)
            print(build.stdout, file=sys.stderr)
            print(build.stderr, file=sys.stderr)
            return build.returncode
        print(build.stdout.strip())

        verify_args = ["--verify", str(tmp_dir)]
        if no_network:
            verify_args.append("--no-network")
        result = _run_inner(*verify_args)
        print(result.stdout.strip())
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
        return result.returncode
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="offline_bundle",
        description="Air-gap bundle gate: build a fresh bundle and verify it offline (P16.7).",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="build a fresh bundle to a temp dir and verify it offline, then clean up",
    )
    parser.add_argument(
        "--no-network",
        action="store_true",
        help="verify under dead proxies (offline proof)",
    )
    args = parser.parse_args(argv)

    if not args.verify:
        parser.error("pass --verify")
        return 2

    return verify(no_network=args.no_network)


if __name__ == "__main__":
    raise SystemExit(main())
