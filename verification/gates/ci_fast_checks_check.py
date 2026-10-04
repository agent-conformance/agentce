"""Driver for VG-CI-FAST-CHECKS (18.37i, AGENTS.md Commands: "These mirror the CI workflows; keep
them in sync"). Not itself a test file: invoked by ci_fast_checks.sh. 18.37's circuit breaker found
that two of its three failures (a verifier round's ruff/mypy/biome findings, and a red CI run from a
stale catalog registry digest) were checks CI runs that `./verification/run --quick` did not --
closing that gap needs more than running the same commands once; it needs the gap itself to stay
closed. This parses every `.github/workflows/*.yml` file (real YAML, `tools/ci_demo_coverage_check.py`'s
own pattern) for each step that runs a fast checker (ruff check, ruff format --check, mypy,
pnpm lint, pnpm typecheck, catalog_registry_check.py or registry_check.py), pairs each matching
line with that step's own `working-directory` (default the repository root), and fails if a
discovered CI step has no matching entry in MANIFEST below, or a MANIFEST entry matches no
discovered CI step. With no drift, MANIFEST is also the one thing this script actually runs: every
entry, for real, in its own directory, with its own exact command text -- there is no second,
separately hand-maintained list of commands anywhere else for it to fall out of sync with.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

CHECKERS: tuple[str, ...] = (
    "ruff check",
    "ruff format --check",
    "mypy",
    "pnpm lint",
    "pnpm typecheck",
    "catalog_registry_check.py",
    "registry_check.py",
)

# A checker name as a real command/filename token, not a substring of something else (".mypy_cache"
# contains "mypy" but is not an invocation of it).
_CHECKER_PATTERNS = {
    checker: re.compile(r"(?<![\w.])" + re.escape(checker) + r"(?!\w)")
    for checker in CHECKERS
}


def _matches_a_checker(line: str) -> bool:
    return any(pattern.search(line) for pattern in _CHECKER_PATTERNS.values())


# The four files verification.yml's own lint/type-check job covers, named once.
_TOOLS_FILES = "verification/run verification/selftest.py verification/probe.py verification/shard.py"

# One entry per real CI invocation of a CHECKERS string, as (cwd, run) -- cwd is "." when the CI
# step sets no working-directory. The single source of truth: also what MANIFEST runs for real
# (run_manifest below), so there is nothing else to keep in sync with it by hand.
MANIFEST: tuple[tuple[str, str], ...] = (
    ("engines/python", "uv run --frozen ruff check ."),
    ("engines/python", "uv run --frozen ruff format --check ."),
    ("engines/python", "uv run --frozen mypy ."),
    ("engines/typescript", "pnpm lint"),
    ("engines/typescript", "pnpm typecheck"),
    (".", "uv run --project corpus --frozen ruff check corpus"),
    (".", "uv run --project corpus --frozen ruff format --check corpus"),
    (".", f"uv run --project tools --frozen ruff check {_TOOLS_FILES}"),
    (".", f"uv run --project tools --frozen ruff format --check {_TOOLS_FILES}"),
    (".", f"uv run --project tools --frozen mypy --strict {_TOOLS_FILES}"),
    ("conformance", "uv run --frozen python catalog_registry_check.py --self-test"),
    ("conformance", "uv run --frozen python registry_check.py --self-test"),
)


def discover_steps(doc: dict[str, Any]) -> list[tuple[str, str]]:
    """Every (cwd, run-line) pair in one parsed workflow document whose run line matches a
    CHECKERS pattern. `working-directory` is read per step, straight from the step's own mapping
    (real YAML structure, no indentation-guessing), default "." when a step sets none."""
    found: list[tuple[str, str]] = []
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            run = step.get("run")
            if not run:
                continue
            cwd = step.get("working-directory", ".")
            for line in run.splitlines():
                line = line.strip()
                if line and _matches_a_checker(line):
                    found.append((cwd, line))
    return found


def discover_workflow_steps(workflows_dir: Path) -> list[tuple[str, str]]:
    """discover_steps over every real `*.yml` under `workflows_dir`."""
    found: list[tuple[str, str]] = []
    for path in sorted(workflows_dir.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        found.extend(discover_steps(doc))
    return found


def compare(
    discovered: list[tuple[str, str]], manifest: tuple[tuple[str, str], ...]
) -> list[str]:
    """Problems between a discovered (cwd, run) multiset and MANIFEST: a discovered pair absent
    from the manifest (an uncovered CI step), or a manifest pair absent from discovered (a stale
    entry CI no longer runs)."""
    discovered_set = set(discovered)
    manifest_set = set(manifest)
    problems = [
        f"uncovered CI step: cwd={cwd!r} run={run!r} has no MANIFEST entry"
        for cwd, run in sorted(discovered_set - manifest_set)
    ]
    problems.extend(
        f"stale MANIFEST entry: cwd={cwd!r} run={run!r} matches no discovered CI step"
        for cwd, run in sorted(manifest_set - discovered_set)
    )
    return problems


def run_commands(root: Path, commands: tuple[tuple[str, str], ...]) -> int:
    """Run every (cwd, run) pair for real, from root/cwd, with VIRTUAL_ENV unset (each `uv run`
    below targets its own project, not whatever venv this script itself is running under). Prints
    each command before running it; returns 1 if any exits non-zero."""
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    status = 0
    for cwd, run in commands:
        print(f"ci-fast-checks: ({cwd}) $ {run}", flush=True)
        proc = subprocess.run(run, shell=True, cwd=root / cwd, env=env, check=False)
        if proc.returncode != 0:
            status = 1
    return status


def self_test() -> int:
    cases: list[tuple[str, bool]] = []

    doc_a = {
        "jobs": {
            "python": {
                "steps": [
                    {
                        "name": "Lint",
                        "working-directory": "foo/bar",
                        "run": "ruff check .",
                    }
                ]
            }
        }
    }
    cases.append(
        (
            "single-line run with working-directory",
            discover_steps(doc_a) == [("foo/bar", "ruff check .")],
        )
    )

    doc_b = {
        "jobs": {
            "python": {
                "steps": [
                    {
                        "name": "Lint",
                        "working-directory": "engines/python",
                        "run": "uv run --frozen ruff check .\nuv run --frozen ruff format --check .\n",
                    },
                    {"name": "Unrelated", "run": "echo hi"},
                    {"name": "Other", "run": "mypy ."},
                ]
            }
        }
    }
    cases.append(
        (
            "multi-line run block found; next step's cwd is not inherited from the previous one",
            discover_steps(doc_b)
            == [
                ("engines/python", "uv run --frozen ruff check ."),
                ("engines/python", "uv run --frozen ruff format --check ."),
                (".", "mypy ."),
            ],
        )
    )

    doc_c = {
        "jobs": {
            "job": {
                "steps": [
                    {"run": "echo .mypy_cache and .ruff_cache are not invocations"}
                ]
            }
        }
    }
    cases.append(
        (
            "a checker name inside another token is not a match",
            discover_steps(doc_c) == [],
        )
    )

    uncovered = compare([("new/dir", "ruff check x")], ())
    cases.append(
        (
            "a discovered pair with an empty manifest is reported uncovered",
            uncovered
            == [
                "uncovered CI step: cwd='new/dir' run='ruff check x' has no MANIFEST entry"
            ],
        )
    )

    stale = compare([], (("old/dir", "mypy y"),))
    cases.append(
        (
            "a manifest pair with nothing discovered is reported stale",
            stale
            == [
                "stale MANIFEST entry: cwd='old/dir' run='mypy y' matches no discovered CI step"
            ],
        )
    )

    matched = compare([("x", "ruff check y")], (("x", "ruff check y"),))
    cases.append(("a matching pair on both sides reports nothing", matched == []))

    root = Path(__file__).resolve().parent.parent.parent
    run_ok = run_commands(root, ((".", "true"),))
    run_fail = run_commands(root, ((".", "false"),))
    cases.append(("run_commands reports 0 for a command that exits 0", run_ok == 0))
    cases.append(
        ("run_commands reports 1 for a command that exits non-zero", run_fail == 1)
    )

    failed = [name for name, ok in cases if not ok]
    if failed:
        for name in failed:
            print(f"ci-fast-checks self-test FAIL: {name}", file=sys.stderr)
        return 1
    print(f"ci-fast-checks self-test: {len(cases)}/{len(cases)} cases OK")
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    root = Path(__file__).resolve().parent.parent.parent
    discovered = discover_workflow_steps(root / ".github" / "workflows")
    problems = compare(discovered, MANIFEST)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(
            f"ci-fast-checks: {len(problems)} problem(s) between the CI workflows and MANIFEST",
            file=sys.stderr,
        )
        return 1
    print(
        f"ci-fast-checks: every one of {len(MANIFEST)} MANIFEST entries matches a real CI step, "
        "and every real CI fast-checker step has a MANIFEST entry"
    )
    return run_commands(root, MANIFEST)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
