"""cryptography_intel_wheel_check -- prove every tracked ``uv.lock`` resolving ``cryptography`` still
resolves an Intel-macOS wheel (item 18.58, ADR-0024).

ADR-0024 split ``engines/python/pyproject.toml`` and ``adapters/supply-chain/pyproject.toml``'s
``cryptography`` requirement by platform marker so Intel macOS resolves the last release with a real
prebuilt wheel (48.x) while every other platform keeps the advisory-clean 50.x line. Every other tracked
``uv.lock`` in the workspace depends on ``agent-conformance`` (editable from ``engines/python``), so its
own re-lock inherits that split -- but only once someone actually runs ``uv lock`` there. 18.55's verifier
found 20 such locks still pinned to plain ``cryptography==50.0.1`` with no Intel wheel (O1): AGENTS.md's
own documented docs and conformance commands would still fail a fresh Intel-macOS install. This script is
the standing check a later dependency bump could otherwise silently regress: it parses every tracked lock
that names ``cryptography`` and checks its resolved wheels for one macOS/x86_64 can actually install.

Usage: ``uv run --project tools python tools/cryptography_intel_wheel_check.py``. Exit 0 when every
tracked lock naming ``cryptography`` resolves an Intel-macOS wheel (``macosx_*_universal2`` or
``macosx_*_x86_64``), exit 1 and print each offending lock otherwise. ``--self-test`` proves the matcher
discriminates a real Intel wheel line from a lock that resolves only Linux/Windows/arm64 wheels.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

#: A cryptography wheel's platform tag that macOS/x86_64 can actually install: the universal2 fat
#: binary, or a bare Intel build. Same two architectures as the item's acceptance check and ADR-0024's
#: verification bullet; the shape mirrors (but is not the same regex as) ``environment.py``'s
#: ``PREBUILT_MACOS_TAG``, which matches a bare tag string (``arm64`` included, no ``x86_64``) read from
#: an installed distribution's own ``WHEEL`` file, not a wheel filename's trailing segment read from a
#: lock. A future wheel-tagging change upstream needs both updated together.
INTEL_TAG = re.compile(r"^macosx_\d+_\d+_(universal2|x86_64)$")


def tracked_uv_locks() -> list[Path]:
    """Every tracked ``uv.lock``, found the way the rest of ``tools/`` lists tracked files (list all,
    filter in Python), not by handing git a pathspec glob."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [ROOT / p for p in out.stdout.split("\0") if p.endswith("uv.lock")]


def _platform_tag(wheel_url: str) -> str:
    """The platform-tag segment of a wheel filename, e.g. ``macosx_10_9_universal2`` from
    ``.../cryptography-48.0.1-cp311-abi3-macosx_10_9_universal2.whl``."""
    filename = wheel_url.rsplit("/", 1)[-1]
    return filename.removesuffix(".whl").rsplit("-", 1)[-1]


def lacks_intel_wheel(lock_text: str) -> bool:
    """True when the lock names ``cryptography`` but resolves no Intel-macOS wheel for it. Parses the
    lock as TOML (as ``tools/no_ml_check.py``'s own ``packages_in_uv_lock`` does) rather than
    pattern-matching the raw text, so a reformatted or reordered lock is read the same way ``uv`` reads
    it."""
    data: dict[str, Any] = tomllib.loads(lock_text)
    packages = [
        pkg for pkg in data.get("package", []) if pkg.get("name") == "cryptography"
    ]
    if not packages:
        return False
    return not any(
        INTEL_TAG.match(_platform_tag(wheel.get("url", "")))
        for pkg in packages
        for wheel in pkg.get("wheels", [])
    )


def check() -> list[str]:
    problems = []
    for lock in tracked_uv_locks():
        if lacks_intel_wheel(lock.read_text(encoding="utf-8")):
            problems.append(str(lock.relative_to(ROOT)))
    return problems


def self_test() -> int:
    with_wheel = (
        '[[package]]\nname = "cryptography"\nversion = "48.0.1"\n'
        "wheels = [\n"
        '    { url = "https://files.pythonhosted.org/packages/aa/cryptography-48.0.1-cp311-abi3-'
        'macosx_10_9_universal2.whl" },\n'
        "]\n"
    )
    without_wheel = (
        '[[package]]\nname = "cryptography"\nversion = "50.0.1"\n'
        "wheels = [\n"
        '    { url = "https://files.pythonhosted.org/packages/bb/cryptography-50.0.1-cp311-abi3-'
        'manylinux_2_28_x86_64.whl" },\n'
        '    { url = "https://files.pythonhosted.org/packages/cc/cryptography-50.0.1-cp311-abi3-'
        'macosx_11_0_arm64.whl" },\n'
        "]\n"
    )
    no_cryptography = '[[package]]\nname = "jsonschema"\nversion = "4.21.0"\n'
    assert not lacks_intel_wheel(with_wheel), (
        "a universal2 wheel line must satisfy the check"
    )
    assert lacks_intel_wheel(without_wheel), (
        "a lock resolving cryptography with only Linux/arm64 wheels must fail the check"
    )
    assert not lacks_intel_wheel(no_cryptography), (
        "a lock that never names cryptography is out of scope, not a failure"
    )
    print("ok: cryptography_intel_wheel_check's matcher discriminates")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="prove the matcher discriminates an Intel wheel from one with no Intel wheel",
    )
    parser.add_argument(
        "--json", action="store_true", help="also print the machine-readable result"
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    problems = check()
    if args.json:
        print(json.dumps({"problems": problems}, indent=2))
    if problems:
        for p in problems:
            print(f"FAIL: no Intel macOS cryptography wheel in {p}", file=sys.stderr)
        return 1
    print("ok: every tracked uv.lock naming cryptography resolves an Intel-macOS wheel")
    return 0


if __name__ == "__main__":
    sys.exit(main())
