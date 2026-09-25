"""Governance and conformance-program gate (SPEC §14.2, §14.5, §8.7, P5.6).

Emits ``{"program", "notice_linked", "registry_refuses", "offline_ecs", "signing_offline",
"terms_clean"}`` for the phase-5 eval's P5.6 check:

* ``program`` -- the governance documents and the conformance program are present;
* ``notice_linked`` -- ``NOTICE`` exists and is linked from the governance documents;
* ``registry_refuses`` -- the implementation-report registry refuses an unverifiable report;
* ``offline_ecs`` -- the offline distribution bundle builds and runs the ECS with networking disabled;
* ``signing_offline`` -- every release signing profile verifies offline against the vendored trust root;
* ``terms_clean`` -- no tracked file carries a private-terms marker.

Only the JSON is written to stdout (the sub-checks are called as functions, never their CLIs), so::

    cd conformance && uv run python governance_check.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

import offline_bundle
import registry_check
import release
from agentce import signing

REPO_ROOT = Path(__file__).resolve().parents[1]
GOVERNANCE = REPO_ROOT / "governance"
NOTICE = REPO_ROOT / "NOTICE"
PRIVATE_TERMS = REPO_ROOT / "SPECS" / "harness" / "private-terms.txt"

#: A public clone never has the real private-terms list above on disk (that tree is excluded from
#: this repository's own git entirely). The terms-clean scan's input is resolved in three steps, each
#: overriding the next: (1) ``AGENTCE_PRIVATE_TERMS``, an explicit override any caller (harness or CI)
#: may set; (2) the path above, when it happens to exist on disk (a maintainer's own working copy
#: during development), so nothing about that existing behaviour changes; (3) the small,
#: public-shippable default below -- distinct from, and much shorter than, the real list -- so the
#: test that exercises this scan can actually run in a public clone's own CI instead of being
#: permanently skipped there.
_DEFAULT_PRIVATE_TERMS = (
    REPO_ROOT / "conformance" / "fixtures" / "default-private-terms.txt"
)


def _private_terms_path() -> Path:
    override = os.environ.get("AGENTCE_PRIVATE_TERMS")
    if override:
        return Path(override)
    if PRIVATE_TERMS.is_file():
        return PRIVATE_TERMS
    return _DEFAULT_PRIVATE_TERMS


_GOVERNANCE_FILES = [
    "README.md",
    "VERSIONING.md",
    "SUPPORT.md",
    "SECURITY-PROCESS.md",
    "CONFORMANCE-PROGRAM.md",
]


def _program() -> bool:
    return all((GOVERNANCE / name).is_file() for name in _GOVERNANCE_FILES)


def _notice_linked() -> bool:
    if not NOTICE.is_file():
        return False
    candidates = [*GOVERNANCE.glob("*.md"), REPO_ROOT / "docs" / "index.md"]
    return any(c.is_file() and "NOTICE" in c.read_text("utf-8") for c in candidates)


def _registry_refuses() -> bool:
    trust = signing.vendored_trust()
    good = registry_check.evaluate_report(
        registry_check._sample_report("sha256:" + "0" * 64), trust
    )
    tampered_report = registry_check._sample_report("sha256:" + "0" * 64)
    tampered_report["golden"]["digest"] = "sha256:" + "1" * 64
    tampered = registry_check.evaluate_report(tampered_report, trust)
    return bool(good["listed"]) and not tampered["listed"]


def _offline_ecs() -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "bundle"
        offline_bundle.build_bundle(out)
        result = offline_bundle.verify_bundle(out, no_network=True)
    return not result["problems"] and bool(result["ran_ecs"])


def _signing_offline() -> bool:
    result = release.run(
        dry_run=True, profiles=list(release.ALL_PROFILES), offline=True, out_dir=None
    )
    return bool(result["signatures_verify"])


def _terms_clean() -> bool:
    terms_path = _private_terms_path()
    if not terms_path.is_file() or terms_path.stat().st_size == 0:
        return False
    proc = subprocess.run(
        [
            "git",
            "grep",
            "-i",
            "-l",
            "-f",
            str(terms_path),
            "--",
            ".",
            ":(exclude)LICENSE*",
            ":(exclude,glob)**/LICENSE*",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # git grep exits 1 with no output when nothing matches: that is the clean case.
    return proc.returncode == 1 and not proc.stdout.strip()


def run() -> dict[str, bool]:
    return {
        "program": _program(),
        "notice_linked": _notice_linked(),
        "registry_refuses": _registry_refuses(),
        "offline_ecs": _offline_ecs(),
        "signing_offline": _signing_offline(),
        "terms_clean": _terms_clean(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="governance_check",
        description="Governance and conformance-program gate (P5.6).",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args(argv)

    result = run()
    ok = all(result.values())
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        for key, value in sorted(result.items()):
            print(f"{key}={value}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
