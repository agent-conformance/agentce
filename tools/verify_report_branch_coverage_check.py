"""verify_report_branch_coverage_check - 100% branch coverage of `_verify_report` alone (18.8.R1).

`_verify_report` (`engines/python/agentce/commands/__init__.py`) has about 15 `raise` sites across
its 9 reproduction steps, several of which share a message key (SPEC §18.8's stage table). A check
that only asserts an exit code and a message key cannot tell two branches apart when they raise the
same key, so three separate verifier rounds on item 18.8 each found live mutations an exit-code/key
check missed (`harness/remediation/journal/18.8.md`, "Recommended ... a future item add a
`coverage.py` branch-coverage gate scoped to `_verify_report` (100% required)"). This is that gate:
it runs the Python engine's `test_verify_report*`-named tests (not a bespoke fixture -- the gate's
job is only to prove every branch is *somewhere* exercised by the suite's own tests) under
`coverage.py`'s branch tracking and requires zero missing lines and zero missing branches inside
`_verify_report` specifically, using `coverage.py`'s own per-function JSON report
(`files[path]["functions"][name]`) rather than a hand-maintained line range, so the check tracks the
function's real body as it is edited. Scoping to `test_verify_report*` rather than the full suite was
confirmed empirically, not assumed: both give the identical missing-branch set for `_verify_report`,
the scoped run in roughly 90 seconds against the full suite's roughly 470.

Usage:
    verify_report_branch_coverage_check.py              # runs the scoped tests, checks the function
    verify_report_branch_coverage_check.py --self-test   # proves the comparator discriminates,
                                                          # without running the real tests
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = REPO_ROOT / "engines" / "python"
TARGET_PATH = "agentce/commands/__init__.py"
FUNCTION_NAME = "_verify_report"


def _judge(func_data: dict[str, Any]) -> tuple[bool, str]:
    """Return (ok, message) for one function's coverage.py per-function report entry."""
    missing_lines = func_data.get("missing_lines", [])
    missing_branches = func_data.get("missing_branches", [])
    if missing_lines or missing_branches:
        return False, (
            f"FAIL: {FUNCTION_NAME} is not at 100% branch coverage -- "
            f"missing lines {missing_lines}, missing branches {missing_branches}"
        )
    summary = func_data.get("summary", {})
    return True, (
        f"PASS: {FUNCTION_NAME} is at 100% line and branch coverage "
        f"({summary.get('num_statements')} statements, {summary.get('num_branches')} branches)"
    )


def self_test() -> int:
    """The comparator alone, no real suite run: a clean report passes, one missing branch fails."""
    clean = {
        "missing_lines": [],
        "missing_branches": [],
        "summary": {"num_statements": 181, "num_branches": 94},
    }
    ok, message = _judge(clean)
    if not ok:
        print(
            f"self-test FAIL: a fully-covered function was judged not covered: {message}"
        )
        return 1
    tampered = {
        "missing_lines": [900],
        "missing_branches": [[899, 900]],
        "summary": {"num_statements": 181, "num_branches": 94},
    }
    ok, message = _judge(tampered)
    if ok:
        print(
            "self-test FAIL: a function with a missing branch was judged fully covered"
        )
        return 1
    print("self-test PASS: comparator discriminates")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()

    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "coverage.json"
        run = subprocess.run(
            [
                "uv",
                "run",
                "--frozen",
                "pytest",
                "tests/test_commands.py",
                "-k",
                "test_verify_report",
                "-q",
                "-p",
                "no:cacheprovider",
                "--cov=agentce",
                "--cov-branch",
                f"--cov-report=json:{report_path}",
                # The repo-wide 90% floor (pyproject.toml) does not apply to this scoped run: only
                # the `test_verify_report*` tests run, so the suite-wide percentage is meaningless
                # here, and the per-function check below is the real gate.
                "--cov-fail-under=0",
            ],
            cwd=ENGINE_DIR,
        )
        if run.returncode != 0:
            print(
                "FAIL: the test_verify_report* tests did not all pass", file=sys.stderr
            )
            return 1
        data = json.loads(report_path.read_text(encoding="utf-8"))

    file_data = data["files"].get(TARGET_PATH)
    if file_data is None:
        print(
            f"FAIL: {TARGET_PATH} is missing from the coverage report", file=sys.stderr
        )
        return 1
    func_data = file_data["functions"].get(FUNCTION_NAME)
    if func_data is None:
        print(
            f"FAIL: {FUNCTION_NAME} is missing from {TARGET_PATH}'s per-function coverage",
            file=sys.stderr,
        )
        return 1
    ok, message = _judge(func_data)
    print(message, file=sys.stderr if not ok else sys.stdout)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
