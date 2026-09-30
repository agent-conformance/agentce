"""skill_release_shape_check - prove the S-10 commit-pinned install still works once phase commits stop
carrying the vendored engine wheel (item 18.54, `skills/README.md` "Install and pin (S-10)").

Everyday phase commits no longer track `skills/*/vendor/*.whl` or `skills/*/uv.lock` (18.54). The wheel
churned on every engine change (MAINTAINER-INBOX row 27). Only the commit a release tag points at still
carries them, built there by `tools/vendor_skill_engine.py --write` as the last step of cutting a release.
S-10 states that a commit-pinned checkout of that commit, whether a zip, an assistant's skill directory,
or a registry checkout, is still one-command installable on its own, severed from this monorepo, with no
sibling `engines/` checkout and no network beyond the one-time dependency install.

This check builds that commit and runs the install it describes, rather than only asserting its text:

    1. `git worktree add --detach` a throwaway checkout of HEAD, standing in for the commit a release tag
       will point at.
    2. Run that worktree's own `tools/vendor_skill_engine.py --write` inside it (the release-time step),
       and commit the result there. That commit is now release-shaped: it carries `vendor/*.whl` and
       `uv.lock` for both skills, exactly as a real release commit would.
    3. Copy each skill folder out of the worktree alone, into its own empty directory with no monorepo
       beside it, and run the two S-10 self-test commands from `skills/README.md` there.

`--no-network` runs step 3 under dead proxies, the same convention `installed_artifacts_check.py` uses:
install steps may reach the network on a cold `uv` cache, every run step is offline. An accidental network
call in the standalone self-test then fails loudly instead of silently succeeding over a live connection.

`--self-test` proves the check discriminates. The good release-shaped commit passes. Two seeded faults, a
release commit with a missing vendored wheel and one with a stale (corrupted) wheel whose bytes no longer
match `uv.lock`'s pinned hash, each turn the standalone self-test red.

Usage:
    skill_release_shape_check.py                 build a release-shaped commit and prove both skills
                                                   install and self-test standalone from it
    skill_release_shape_check.py --no-network     the same, with the standalone self-test run offline
    skill_release_shape_check.py --self-test      prove a missing or stale wheel on the release commit
                                                   turns the check red
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ("agentce-get-evidence", "agentce-prepare-to-share")
STRIP_DIRS = {".venv", ".mypy_cache", ".ruff_cache", "__pycache__"}

#: A dead proxy: any accidental network call fails, proving the standalone self-test runs offline
#: (the same device `conformance/offline_bundle.py` uses for its own offline proof).
_DEAD_PROXY = "http://127.0.0.1:9"

SELF_TEST_CMDS: dict[str, list[str]] = {
    "agentce-get-evidence": ["uv", "run", "--frozen", "python3", "scripts/lint_profile.py", "--self-test", "--json"],
    "agentce-prepare-to-share": ["uv", "run", "--frozen", "pytest", "-q", "-p", "no:cacheprovider"],
}


class CheckFailure(Exception):
    """A real, named assertion failed."""


def _run(cmd: list[str], *, cwd: Path, no_network: bool = False) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if no_network:
        env.update(
            HTTP_PROXY=_DEAD_PROXY,
            HTTPS_PROXY=_DEAD_PROXY,
            http_proxy=_DEAD_PROXY,
            https_proxy=_DEAD_PROXY,
            NO_PROXY="",
        )
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


def _git(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd or ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        raise CheckFailure(f"git {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout.strip()


def build_release_worktree(tmp: Path, *, vendor: bool = True) -> Path:
    """Check out HEAD into a throwaway worktree and, unless `vendor` is False, vendor+commit it.

    `vendor=False` seeds the "release tooling forgot to build the wheel" fault for --self-test: the
    worktree is committed exactly as HEAD already is, with no wheel at all.
    """
    worktree = tmp / "release-worktree"
    _git("worktree", "add", "--detach", str(worktree), "HEAD")
    if vendor:
        proc = subprocess.run(
            [sys.executable, str(worktree / "tools" / "vendor_skill_engine.py"), "--write"],
            cwd=worktree,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise CheckFailure(f"vendor_skill_engine.py --write failed in the worktree:\n{proc.stdout}\n{proc.stderr}")
        _git("add", "-A", "--", "skills", cwd=worktree)
        status = _git("status", "--porcelain", "--", "skills", cwd=worktree)
        if status:
            _git(
                "-c", "user.name=skill-release-shape-check", "-c", "user.email=skill-release-shape-check@localhost",
                "commit", "-m", "release: vendor the engine wheel for this release commit", "--no-verify",
                cwd=worktree,
            )
    return worktree


def stale_wheel_worktree(tmp: Path) -> Path:
    """A release-shaped commit whose vendored wheel bytes no longer match `uv.lock`'s pinned hash."""
    worktree = build_release_worktree(tmp, vendor=True)
    for skill in SKILLS:
        wheels = sorted((worktree / "skills" / skill / "vendor").glob("*.whl"))
        if not wheels:
            raise CheckFailure(f"self-test setup: no vendored wheel to corrupt for {skill}")
        wheels[0].write_bytes(b"not a real wheel")
    return worktree


def copy_skill_out(worktree: Path, skill: str, dest_root: Path) -> Path:
    """Copy one skill folder out of `worktree`, severed from the monorepo, into its own empty directory."""
    dest = dest_root / skill
    shutil.copytree(
        worktree / "skills" / skill,
        dest,
        ignore=shutil.ignore_patterns(*STRIP_DIRS),
    )
    return dest


def run_standalone_self_test(skill_dir: Path, skill: str, *, no_network: bool) -> dict[str, Any]:
    proc = _run(SELF_TEST_CMDS[skill], cwd=skill_dir, no_network=no_network)
    ok = proc.returncode == 0
    if ok and skill == "agentce-get-evidence":
        ok = '"self_test": "ok"' in proc.stdout
    return {
        "skill": skill,
        "ok": ok,
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }


def check(*, no_network: bool) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="agentce-release-shape-") as raw:
        tmp = Path(raw)
        try:
            worktree = build_release_worktree(tmp)
            results = []
            for skill in SKILLS:
                skill_dir = copy_skill_out(worktree, skill, tmp / "installed")
                results.append(run_standalone_self_test(skill_dir, skill, no_network=no_network))
        finally:
            _git("worktree", "remove", "--force", str(tmp / "release-worktree"))
            _git("worktree", "prune")
    problems = [f"{r['skill']}: self-test failed (exit {r['returncode']})" for r in results if not r["ok"]]
    return {"no_network": no_network, "results": results, "problems": problems}


def _assert_fault_is_red(tmp: Path, worktree: Path, *, label: str) -> None:
    results = []
    for skill in SKILLS:
        skill_dir = copy_skill_out(worktree, skill, tmp / f"installed-{label}")
        results.append(run_standalone_self_test(skill_dir, skill, no_network=False))
    if all(r["ok"] for r in results):
        raise CheckFailure(f"{label}: the standalone self-test passed with no working vendored wheel")


def self_test() -> int:
    failures: list[str] = []

    good = check(no_network=True)
    if good["problems"]:
        failures.append(f"a correctly release-shaped commit unexpectedly failed: {good['problems']}")

    with tempfile.TemporaryDirectory(prefix="agentce-release-shape-selftest-") as raw:
        tmp = Path(raw)
        try:
            missing_wheel_worktree = build_release_worktree(tmp, vendor=False)
            try:
                _assert_fault_is_red(tmp, missing_wheel_worktree, label="missing-wheel")
            except CheckFailure as exc:
                failures.append(str(exc))
        finally:
            _git("worktree", "remove", "--force", str(tmp / "release-worktree"))
            _git("worktree", "prune")

    with tempfile.TemporaryDirectory(prefix="agentce-release-shape-selftest-") as raw:
        tmp = Path(raw)
        try:
            stale_worktree = stale_wheel_worktree(tmp)
            try:
                _assert_fault_is_red(tmp, stale_worktree, label="stale-wheel")
            except CheckFailure as exc:
                failures.append(str(exc))
        finally:
            _git("worktree", "remove", "--force", str(tmp / "release-worktree"))
            _git("worktree", "prune")

    if failures:
        for f in failures:
            print(f"SELF-TEST FAIL: {f}", file=sys.stderr)
        return 1
    print(
        "self-test ok: a release-shaped commit installs standalone; a missing or stale vendored wheel "
        "turns the standalone self-test red"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--self-test", action="store_true", help="prove a missing/stale wheel turns the check red")
    parser.add_argument("--no-network", action="store_true", help="run the standalone self-test under dead proxies")
    parser.add_argument("--json", action="store_true", help="also print the machine-readable result")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    result = check(no_network=args.no_network)
    if args.json:
        print(json.dumps(result, indent=2))
    if result["problems"]:
        for p in result["problems"]:
            print(f"FAIL: {p}", file=sys.stderr)
        return 1
    print(
        "ok: a release-shaped commit carries a working vendored wheel; both skills install and "
        f"self-test standalone{' offline' if args.no_network else ''}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
