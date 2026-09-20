"""The release dry-run gate (SPEC §8.7, P5.2).

Emits ``{"reproducible": bool, "sbom_valid": bool, "signatures_verify": bool, "no_publish": bool, …}``
for the phase-5 eval's P5.2 check: two builds produce identical digests, the SBOM validates, every
signing profile verifies with the development identity offline, the stub registry proves no
publish call was made, and the freshly built wheel and sdist install outside the checkout and run
``agentce quickstart`` from an empty directory with the network cut off (the installed-artifact check).
It is a thin front for :mod:`release`; run it as::

    cd conformance && uv run python release_dryrun.py --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from release import ALL_PROFILES, run

_INSTALLED_CHECK = (
    Path(__file__).resolve().parents[1] / "tools" / "installed_artifacts_check.py"
)


def installed_artifacts_run() -> bool:
    """Build the Python artifacts and run them from an empty directory, offline (byte-identical outputs)."""
    proc = subprocess.run(
        [sys.executable, str(_INSTALLED_CHECK), "python", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout or proc.stderr, file=sys.stderr)
    return proc.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release_dryrun",
        description="Release dry-run gate over all signing profiles (SPEC §8.7, P5.2).",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args(argv)

    result = run(dry_run=True, profiles=list(ALL_PROFILES), offline=True, out_dir=None)
    out = {
        "reproducible": result["reproducible"],
        "sbom_valid": result["sbom_valid"],
        "signatures_verify": result["signatures_verify"],
        "no_publish": result["no_publish"],
        "vex_valid": result["vex_valid"],
        "installed_artifacts": installed_artifacts_run(),
        "profiles": result["profiles"],
        "release_digest": result["release_digest"],
    }
    ok = (
        out["reproducible"]
        and out["sbom_valid"]
        and out["signatures_verify"]
        and out["no_publish"]
        and out["installed_artifacts"]
    )
    if args.json:
        print(json.dumps(out, sort_keys=True))
    else:
        print(
            f"reproducible={out['reproducible']} sbom_valid={out['sbom_valid']} "
            f"signatures_verify={out['signatures_verify']} no_publish={out['no_publish']} "
            f"installed_artifacts={out['installed_artifacts']}"
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
