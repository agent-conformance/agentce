"""skill_release_shape_check - prove the S-10 commit-pinned install still works once phase commits stop
carrying the vendored engine wheel (item 18.54, `skills/README.md` "Install and pin (S-10)").

Everyday phase commits no longer track `skills/*/vendor/*.whl` or `skills/*/uv.lock` (18.54). The wheel
churned on every engine change (MAINTAINER-INBOX row 27). Only the commit a release tag points at still
carries them, force-added there as the last step of cutting a release (`skills/README.md` "Install and
pin (S-10)"). S-10 states that a commit-pinned checkout of that commit, whether a zip, an assistant's
skill directory, or a registry checkout, is still one-command installable on its own, severed from this
monorepo, with no sibling `engines/` checkout and no network beyond the one-time dependency install.

This check builds that commit and runs the install it describes, rather than only asserting its text:

    1. `git worktree add --detach` a throwaway checkout of HEAD, standing in for the commit a release tag
       will point at.
    2. Run that worktree's own `tools/vendor_skill_engine.py --write` inside it (the release-time build
       step), asserting no tracked file other than the gitignored wheel and lock came out dirty. A version
       bump landed in an earlier phase commit with no matching `--write` would otherwise leave a skill's
       `pyproject.toml` stale and uncommitted -- a mismatch against the wheel this step is about to
       commit, one only a non-frozen install downstream would ever notice. Then force-add the gitignored
       wheel and lock for both skills and commit them. `git ls-tree` on the resulting commit confirms
       every expected path actually landed in it -- `.gitignore` makes a plain `git add -A` silently skip
       these paths, so a commit built that way would carry none of them while still reporting success;
       this check fails loudly if that ever regresses. A second clean-tree assertion right after the
       commit confirms the worktree's files are now byte-identical to what the commit carries, which is
       what lets step 4 below trust the worktree as a stand-in for the commit.
    3. Extract each skill folder from that commit with `git archive` (the committed tree, never the
       worktree's working directory, which could carry stray uncommitted files) into its own empty
       directory with no monorepo beside it, and run the two S-10 self-test commands from
       `skills/README.md` there.
    4. Rebuild each skill's wheel fresh from the release commit's own `engines/python` and diff it against
       the committed vendored wheel (reusing `vendor_skill_engine.py`'s own check mode) -- a release commit
       whose wheel was vendored before a later, unvendored engine change landed in the same commit is
       stale, and the S-10 self-tests alone would not catch it (a stale-but-valid wheel still installs and
       runs).
    5. Confirm `ci.yml`'s `agent-skill-tests` job still runs these in order: vendor, then self-test/check,
       then this script.

`--no-network` runs step 3 under dead proxies, the same convention `installed_artifacts_check.py` uses:
install steps may reach the network on a cold `uv` cache, every run step is offline. An accidental network
call in the standalone self-test then fails loudly instead of silently succeeding over a live connection.

`--self-test` proves the check discriminates. The good release-shaped commit passes. Four seeded faults
each turn it red: a release commit with no vendored wheel at all, one with a corrupted wheel (bytes that
no longer form a valid zip), one whose wheel was vendored before a later engine-source change was folded
into the same commit without re-vendoring (a real, valid wheel -- stale, not broken), and an attempt to
cut a release on top of a phase commit that bumped the engine version without ever running `--write`
(the release step refuses outright rather than committing a wheel and lock next to a stale pyproject.toml).

Usage:
    skill_release_shape_check.py                 build a release-shaped commit and prove both skills
                                                   install and self-test standalone from it
    skill_release_shape_check.py --no-network     the same, with the standalone self-test run offline
    skill_release_shape_check.py --self-test      prove a missing, corrupted or stale wheel, or a
                                                   drifted pyproject.toml, on the release commit turns
                                                   the check red
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ("agentce-get-evidence", "agentce-prepare-to-share")

#: A dead proxy: any accidental network call fails, proving the standalone self-test runs offline
#: (the same device `conformance/offline_bundle.py` uses for its own offline proof).
_DEAD_PROXY = "http://127.0.0.1:9"

SELF_TEST_CMDS: dict[str, list[str]] = {
    "agentce-get-evidence": [
        "uv",
        "run",
        "--frozen",
        "python3",
        "scripts/lint_profile.py",
        "--self-test",
        "--json",
    ],
    "agentce-prepare-to-share": [
        "uv",
        "run",
        "--frozen",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
    ],
}


class CheckFailure(Exception):
    """A real, named assertion failed."""


def _run(
    cmd: list[str], *, cwd: Path, no_network: bool = False
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if no_network:
        env.update(
            HTTP_PROXY=_DEAD_PROXY,
            HTTPS_PROXY=_DEAD_PROXY,
            http_proxy=_DEAD_PROXY,
            https_proxy=_DEAD_PROXY,
            NO_PROXY="",
            UV_OFFLINE="1",
        )
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


def _git(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd or ROOT, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise CheckFailure(
            f"git {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}"
        )
    return proc.stdout.strip()


def _commit_as_bot(worktree: Path, *args: str) -> None:
    """Run `git commit` in `worktree` under a fixed throwaway identity (these commits never leave the
    temporary worktree, so no real author applies)."""
    _git(
        "-c",
        "user.name=skill-release-shape-check",
        "-c",
        "user.email=skill-release-shape-check@localhost",
        "commit",
        *args,
        "--no-verify",
        cwd=worktree,
    )


def _remove_worktree(worktree: Path) -> None:
    _git("worktree", "remove", "--force", str(worktree))
    _git("worktree", "prune")


def _assert_worktree_clean(worktree: Path, *, context: str) -> None:
    """Refuse to proceed if any tracked file in the whole worktree differs from HEAD (the gitignored
    vendor wheel and lock never show here). Catches the shape contract-critic round 2 found: `--write`
    silently rewrites a skill's `pyproject.toml` source line to match a wheel filename that was never
    committed, when an engine version bump landed in an earlier phase commit with no matching `--write`
    + commit. Checking the whole worktree, not only `skills/`, closes the general class -- any tracked
    file `--write` or the release commit leaves modified and uncommitted, not only `pyproject.toml`.
    Without this check, the release step would commit an in-sync wheel and lock next to a stale,
    uncommitted file -- a mismatch only a non-frozen install, never exercised here, would surface."""
    dirty = _git("status", "--porcelain", cwd=worktree)
    if dirty:
        raise CheckFailure(
            f"{context}: tracked files changed beyond the gitignored vendor/lock artifacts -- commit "
            f"these as a normal phase commit first, then cut the release:\n{dirty}"
        )


def _release_paths(worktree: Path) -> list[str]:
    """The exact paths the release step must land in the commit: each skill's lock and wheel."""
    paths: list[str] = []
    for skill in SKILLS:
        paths.append(f"skills/{skill}/uv.lock")
        wheels = sorted((worktree / "skills" / skill / "vendor").glob("*.whl"))
        if not wheels:
            raise CheckFailure(f"no vendored wheel to commit for {skill}")
        paths.append(f"skills/{skill}/vendor/{wheels[0].name}")
    return paths


def _commit_release_artifacts(worktree: Path, *, message: str) -> None:
    """Force-add the gitignored vendor wheel and lock for both skills and commit them onto HEAD.

    `.gitignore` excludes `skills/*/vendor/*.whl` and `skills/*/uv.lock` on everyday phase commits
    (18.54); the release procedure documented in `skills/README.md` "Install and pin (S-10)" force-adds
    exactly these paths as its last step before tagging. Force-add them by name rather than `git add -A`,
    which silently skips gitignored files and would leave the release commit carrying none of them while
    the `git add` and `git commit` calls both still exit 0.
    """
    _assert_worktree_clean(worktree, context="before the release commit")
    paths = _release_paths(worktree)
    _git("add", "-f", "--", *paths, cwd=worktree)
    status = _git("status", "--porcelain", "--", *paths, cwd=worktree)
    if not status:
        raise CheckFailure(
            "nothing staged for the release commit -- git add -f found no changes"
        )
    _commit_as_bot(worktree, "-m", message)
    tracked = set(
        _git("ls-tree", "-r", "--name-only", "HEAD", cwd=worktree).splitlines()
    )
    missing = [p for p in paths if p not in tracked]
    if missing:
        raise CheckFailure(
            f"release commit is missing {missing} from its tree even after git add -f"
        )
    _assert_worktree_clean(worktree, context="after the release commit")


def build_release_worktree(
    tmp: Path, *, vendor: bool = True, ref: str = "HEAD"
) -> Path:
    """Check out `ref` into a throwaway worktree and, unless `vendor` is False, vendor+commit it.

    `vendor=False` seeds the "release tooling forgot to build the wheel" fault for --self-test: the
    worktree is committed exactly as `ref` already is, with no wheel at all. `ref` defaults to `HEAD`;
    `--self-test`'s drifted-pyproject fault points it at a throwaway commit instead.
    """
    worktree = tmp / "release-worktree"
    _git("worktree", "add", "--detach", str(worktree), ref)
    if vendor:
        proc = subprocess.run(
            [
                sys.executable,
                str(worktree / "tools" / "vendor_skill_engine.py"),
                "--write",
            ],
            cwd=worktree,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise CheckFailure(
                f"vendor_skill_engine.py --write failed in the worktree:\n{proc.stdout}\n{proc.stderr}"
            )
        _commit_release_artifacts(
            worktree, message="release: vendor the engine wheel for this release commit"
        )
    return worktree


def corrupted_wheel_worktree(tmp: Path) -> Path:
    """A release-shaped commit whose committed wheel bytes no longer form a valid zip. Amended into the
    release commit itself (not left as an uncommitted, on-disk change), since step 3 now reads the
    committed tree (`git archive`), not the worktree's working directory."""
    worktree = build_release_worktree(tmp, vendor=True)
    corrupted: list[str] = []
    for skill in SKILLS:
        wheels = sorted((worktree / "skills" / skill / "vendor").glob("*.whl"))
        if not wheels:
            raise CheckFailure(
                f"self-test setup: no vendored wheel to corrupt for {skill}"
            )
        wheels[0].write_bytes(b"not a real wheel")
        corrupted.append(f"skills/{skill}/vendor/{wheels[0].name}")
    _git("add", "--", *corrupted, cwd=worktree)
    _commit_as_bot(worktree, "--amend", "--no-edit")
    return worktree


def stale_wheel_worktree(tmp: Path) -> Path:
    """A release-shaped commit whose wheel was vendored before a later engine-source change was folded
    into the same commit without re-vendoring: a real, installable wheel, but stale against the commit's
    own `engines/python`. Amended into the release commit itself, not a follow-up commit, so the sync
    check (step 4) is what has to catch it -- the S-10 self-tests would pass on a stale-but-valid wheel.
    """
    worktree = build_release_worktree(tmp, vendor=True)
    marker = worktree / "engines" / "python" / "agentce" / "__init__.py"
    marker.write_text(
        marker.read_text(encoding="utf-8")
        + "\n# skill_release_shape_check stale-wheel self-test marker\n",
        encoding="utf-8",
    )
    _git("add", "--", "engines/python/agentce/__init__.py", cwd=worktree)
    _commit_as_bot(worktree, "--amend", "--no-edit")
    return worktree


def _assert_drifted_pyproject_is_refused(tmp: Path) -> None:
    """Seed the exact fault contract-critic round 2 found: a phase commit bumps `engines/python`'s
    version without ever running `--write`, so a skill's already-committed `pyproject.toml` still names
    the old wheel filename. The standalone self-test and the sync check can't see this -- `--frozen`
    installs trust `uv.lock`, not `pyproject.toml`'s source line -- so assert the release step refuses to
    build on top of the drift instead, rather than silently committing a release commit whose
    `pyproject.toml` and vendored wheel disagree."""
    drift_source = tmp / "drift-source"
    _git("worktree", "add", "--detach", str(drift_source), "HEAD")
    try:
        pyproject = drift_source / "engines" / "python" / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        bumped = text.replace('version = "0.1.0"', 'version = "0.1.0+selftest"', 1)
        if bumped == text:
            raise CheckFailure(
                "self-test setup: could not find engines/python/pyproject.toml's version line to bump"
            )
        pyproject.write_text(bumped, encoding="utf-8")
        _git("add", "--", "engines/python/pyproject.toml", cwd=drift_source)
        _commit_as_bot(
            drift_source,
            "-m",
            "self-test: bump the engine version with no matching --write",
        )
        drift_sha = _git("rev-parse", "HEAD", cwd=drift_source)
    finally:
        _remove_worktree(drift_source)

    try:
        build_release_worktree(tmp, vendor=True, ref=drift_sha)
    except CheckFailure:
        return
    finally:
        _remove_worktree(tmp / "release-worktree")
    raise CheckFailure(
        "drifted-pyproject: the release step committed a wheel/lock next to a pyproject.toml it never "
        "updated, instead of refusing"
    )


def copy_skill_out(
    worktree: Path, skill: str, dest_root: Path, *, ref: str = "HEAD"
) -> Path:
    """Extract one skill folder from `ref`'s committed tree in `worktree`, severed from the monorepo.

    `git archive` reads only what is actually committed at `ref` -- unlike copying the worktree's working
    directory, a file that `_commit_release_artifacts` failed to commit (or that sits there stale from a
    prior step) cannot leak into the standalone install this proves.
    """
    dest = dest_root / skill
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "archive", ref, "--", f"skills/{skill}"],
        cwd=worktree,
        capture_output=True,
    )
    if archive.returncode != 0:
        raise CheckFailure(
            f"git archive failed for {skill}: {archive.stderr.decode(errors='replace')}"
        )
    extract = subprocess.run(
        ["tar", "-x", "-C", str(dest), "--strip-components=2"],
        input=archive.stdout,
        capture_output=True,
    )
    if extract.returncode != 0:
        raise CheckFailure(
            f"extracting {skill}'s archive failed: {extract.stderr.decode(errors='replace')}"
        )
    return dest


def run_standalone_self_test(
    skill_dir: Path, skill: str, *, no_network: bool
) -> dict[str, Any]:
    """Sync the skill's `.venv` online (a cold `uv` cache may need to fetch wheels), then run its
    self-test command under `no_network`. A single combined `uv run --frozen ...` conflates the two --
    fine on a warm cache, but blocks a real first-time install on a cold one, which is exactly the
    scenario S-10 promises works. `installed_artifacts_check.py` and `offline_bundle.py` use the same
    split: install may reach the network, every run step is offline."""
    sync = _run(["uv", "sync", "--frozen"], cwd=skill_dir, no_network=False)
    if sync.returncode != 0:
        return {
            "skill": skill,
            "ok": False,
            "returncode": sync.returncode,
            "stdout_tail": f"[uv sync --frozen]\n{sync.stdout[-2000:]}",
            "stderr_tail": f"[uv sync --frozen]\n{sync.stderr[-2000:]}",
        }
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


def check_release_commit_in_sync(worktree: Path) -> list[str]:
    """Rebuild each skill's wheel fresh from the release commit's own `engines/python` and diff it
    against the committed vendored wheel (`vendor_skill_engine.py`'s own check mode). Runs against the
    worktree's checked-out files rather than a `git archive` extraction, which is only a faithful stand-in
    for the commit because `_commit_release_artifacts` already asserted the worktree carries no
    uncommitted changes (tracked or gitignored) right after making the commit -- so the files on disk here
    are byte-identical to what `HEAD` actually carries, not merely what happened to be built nearby."""
    proc = subprocess.run(
        [sys.executable, str(worktree / "tools" / "vendor_skill_engine.py")],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return [
            f"release commit's vendored wheel is stale against its own engines/python:\n{proc.stdout}{proc.stderr}"
        ]
    return []


def check_ci_step_order() -> list[str]:
    """`ci.yml`'s `agent-skill-tests` job must run the vendor-write step before either skill's
    self-test/check step, which must run before this script's own CI invocation -- a future edit that
    reorders them would silently stop exercising a freshly built wheel."""
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["agent-skill-tests"]["steps"]
    names = [step.get("name", "") for step in steps]

    def _first_index(needle: str) -> int | None:
        for i, name in enumerate(names):
            if needle in name:
                return i
        return None

    vendor_i = _first_index("Vendor the engine wheel for this run")
    sync_i = _first_index("matches a fresh build")
    release_i = _first_index("release-shaped commit still installs")
    if vendor_i is None or sync_i is None or release_i is None:
        return [
            f"could not find all three agent-skill-tests CI steps by name (vendor={vendor_i}, sync={sync_i}, release-shape={release_i})"
        ]
    if not (vendor_i < sync_i < release_i):
        return [
            f"agent-skill-tests CI step order regressed: vendor={vendor_i}, sync={sync_i}, release-shape={release_i} (expected vendor < sync < release-shape)"
        ]
    return []


def check(*, no_network: bool) -> dict[str, Any]:
    ci_problems = check_ci_step_order()
    with tempfile.TemporaryDirectory(prefix="agentce-release-shape-") as raw:
        tmp = Path(raw)
        try:
            worktree = build_release_worktree(tmp)
            sync_problems = check_release_commit_in_sync(worktree)
            results = []
            for skill in SKILLS:
                skill_dir = copy_skill_out(worktree, skill, tmp / "installed")
                results.append(
                    run_standalone_self_test(skill_dir, skill, no_network=no_network)
                )
        finally:
            _remove_worktree(tmp / "release-worktree")
    problems = (
        ci_problems
        + sync_problems
        + [
            f"{r['skill']}: self-test failed (exit {r['returncode']})"
            for r in results
            if not r["ok"]
        ]
    )
    return {"no_network": no_network, "results": results, "problems": problems}


def _assert_fault_is_red(
    tmp: Path, worktree: Path, *, label: str, check_sync: bool = False
) -> None:
    if check_sync:
        if not check_release_commit_in_sync(worktree):
            raise CheckFailure(
                f"{label}: the sync check passed on a release commit that should be stale"
            )
        return
    results = []
    for skill in SKILLS:
        skill_dir = copy_skill_out(worktree, skill, tmp / f"installed-{label}")
        results.append(run_standalone_self_test(skill_dir, skill, no_network=False))
    if all(r["ok"] for r in results):
        raise CheckFailure(
            f"{label}: the standalone self-test passed with no working vendored wheel"
        )


def _run_fault(build: Any, *, label: str, check_sync: bool = False) -> str | None:
    with tempfile.TemporaryDirectory(
        prefix=f"agentce-release-shape-selftest-{label}-"
    ) as raw:
        tmp = Path(raw)
        try:
            worktree = build(tmp)
            try:
                _assert_fault_is_red(tmp, worktree, label=label, check_sync=check_sync)
            except CheckFailure as exc:
                return str(exc)
        finally:
            _remove_worktree(tmp / "release-worktree")
    return None


def self_test() -> int:
    failures: list[str] = []

    good = check(no_network=True)
    if good["problems"]:
        tails = "\n".join(
            f"--- {r['skill']} (exit {r['returncode']}) ---\nstdout: {r['stdout_tail']}\nstderr: {r['stderr_tail']}"
            for r in good["results"]
            if not r["ok"]
        )
        failures.append(
            f"a correctly release-shaped commit unexpectedly failed: {good['problems']}\n{tails}"
        )

    for label, build, check_sync in (
        ("missing-wheel", lambda tmp: build_release_worktree(tmp, vendor=False), False),
        ("corrupted-wheel", corrupted_wheel_worktree, False),
        ("stale-wheel", stale_wheel_worktree, True),
    ):
        failure = _run_fault(build, label=label, check_sync=check_sync)
        if failure:
            failures.append(failure)

    with tempfile.TemporaryDirectory(
        prefix="agentce-release-shape-selftest-drifted-pyproject-"
    ) as raw:
        try:
            _assert_drifted_pyproject_is_refused(Path(raw))
        except CheckFailure as exc:
            failures.append(str(exc))

    if failures:
        for f in failures:
            print(f"SELF-TEST FAIL: {f}", file=sys.stderr)
        return 1
    print(
        "self-test ok: a release-shaped commit installs standalone; a missing, corrupted or stale "
        "vendored wheel turns the standalone self-test or the sync check red; a release cut on top of a "
        "drifted, uncommitted pyproject.toml is refused outright"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--self-test",
        action="store_true",
        help="prove a missing/corrupted/stale wheel turns the check red",
    )
    parser.add_argument(
        "--no-network",
        action="store_true",
        help="run the standalone self-test under dead proxies",
    )
    parser.add_argument(
        "--json", action="store_true", help="also print the machine-readable result"
    )
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
        "ok: a release-shaped commit carries a working, in-sync vendored wheel; both skills install and "
        f"self-test standalone{' offline' if args.no_network else ''}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
