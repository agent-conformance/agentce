"""verify_report_branch_coverage_check - 100% branch coverage of `_verify_report` alone (18.8.R1, 18.75).

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

coverage.py reports a function nested inside `_verify_report` as its own region
(`_verify_report._has_expect_keyid`), so every such region is held to the same 100% (18.75). Its own
escape hatches are closed too (18.75): a line coverage.py excludes (`# pragma: no cover`) or treats as
a partial branch (`# pragma: no branch`) is not counted as missing, so a pragma would let an untested
branch pass. The gate reads the effective exclusion and partial-branch patterns from coverage.py's own
configuration in the engine's environment and fails on any line of `_verify_report`'s source span
(decorators to last line, from `ast`) that matches one, and on any excluded line the report lists.

Usage:
    verify_report_branch_coverage_check.py              # runs the scoped tests, checks the function
    verify_report_branch_coverage_check.py --self-test   # proves the comparator and the pragma scan
                                                          # discriminate, without running the real tests
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = REPO_ROOT / "engines" / "python"
TARGET_PATH = "agentce/commands/__init__.py"
FUNCTION_NAME = "_verify_report"

# coverage.py's effective configuration in the engine's own environment, read the way pytest-cov reads
# it (the same config files, the same defaults), so a pattern added in pyproject.toml is scanned too.
_PATTERNS_SCRIPT = (
    "import coverage, json; c = coverage.Coverage(); "
    "print(json.dumps({'exclude': c.config.exclude_list, 'partial': c.config.partial_list}))"
)


def _coverage_patterns() -> dict[str, list[str]]:
    out = subprocess.run(
        ["uv", "run", "--frozen", "python", "-c", _PATTERNS_SCRIPT],
        cwd=ENGINE_DIR,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)


def _function_span(source: str, name: str) -> tuple[int, int] | None:
    """The 1-based line span of the first function called `name`, decorators included."""
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == name
        ):
            start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            return start, node.end_lineno or node.lineno
    return None


def _escape_hatches(
    source: str, span: tuple[int, int], patterns: dict[str, list[str]]
) -> list[str]:
    """Each line in `span` that coverage.py would exclude or treat as a partial branch."""
    lines = source.splitlines()
    found = []
    for lineno in range(span[0], span[1] + 1):
        text = lines[lineno - 1]
        for kind, regexes in patterns.items():
            for regex in regexes:
                if re.search(regex, text):
                    found.append(
                        f"line {lineno} matches coverage.py's {kind} pattern {regex!r}: {text.strip()}"
                    )
    return found


def _judge(functions: dict[str, Any]) -> tuple[bool, str]:
    """Return (ok, message) for `_verify_report`'s region and every region nested in it."""
    regions = {
        name: data
        for name, data in functions.items()
        if name == FUNCTION_NAME or name.startswith(FUNCTION_NAME + ".")
    }
    if FUNCTION_NAME not in regions:
        return (
            False,
            f"FAIL: {FUNCTION_NAME} is missing from {TARGET_PATH}'s per-function coverage",
        )
    gaps = [
        f"{name}: missing lines {data.get('missing_lines', [])}, "
        f"missing branches {data.get('missing_branches', [])}, "
        f"excluded lines {data.get('excluded_lines', [])}"
        for name, data in sorted(regions.items())
        if data.get("missing_lines")
        or data.get("missing_branches")
        or data.get("excluded_lines")
    ]
    if gaps:
        return False, (
            f"FAIL: {FUNCTION_NAME} is not at 100% branch coverage with nothing excluded -- "
            + "; ".join(gaps)
        )
    statements = sum(
        d.get("summary", {}).get("num_statements", 0) for d in regions.values()
    )
    branches = sum(
        d.get("summary", {}).get("num_branches", 0) for d in regions.values()
    )
    return True, (
        f"PASS: {FUNCTION_NAME} and its {len(regions) - 1} nested function(s) are at 100% line and "
        f"branch coverage ({statements} statements, {branches} branches), with no line excluded"
    )


def self_test() -> int:
    """The comparator and the pragma scan alone, no real suite run: each must discriminate."""
    clean: dict[str, Any] = {
        "missing_lines": [],
        "missing_branches": [],
        "excluded_lines": [],
    }
    gap: dict[str, Any] = {
        "missing_lines": [900],
        "missing_branches": [[899, 900]],
        "excluded_lines": [],
    }
    excluded: dict[str, Any] = {
        "missing_lines": [],
        "missing_branches": [],
        "excluded_lines": [901],
    }
    cases: list[tuple[str, dict[str, Any], bool]] = [
        (
            "a fully-covered function",
            {FUNCTION_NAME: clean, f"{FUNCTION_NAME}.inner": clean},
            True,
        ),
        ("a function with a missing branch", {FUNCTION_NAME: gap}, False),
        (
            "a nested function with a missing branch",
            {FUNCTION_NAME: clean, f"{FUNCTION_NAME}.inner": gap},
            False,
        ),
        ("a function with an excluded line", {FUNCTION_NAME: excluded}, False),
        ("a report without the function", {"other": clean}, False),
    ]
    for label, functions, want in cases:
        ok, message = _judge(functions)
        if ok is not want:
            print(
                f"self-test FAIL: {label} was judged {'covered' if ok else 'not covered'}: {message}"
            )
            return 1

    patterns = _coverage_patterns()
    body = "def _verify_report(x):\n    if x:\n        return 1\n    return 2\n"
    outside = (
        "def other(x):\n    if x:  # pragma: no cover\n        return 1\n    return 2\n"
    )
    scans = [
        ("a clean function", body, False),
        (
            "a pragma: no cover",
            body.replace("return 1", "return 1  # pragma: no cover"),
            True,
        ),
        (
            "a pragma: no branch",
            body.replace("if x:", "if x:  # pragma: no branch"),
            True,
        ),
        (
            "a pragma on the def line",
            body.replace("(x):", "(x):  # pragma: no cover"),
            True,
        ),
        ("a pragma in another function only", body + outside, False),
    ]
    for label, source, want in scans:
        span = _function_span(source, FUNCTION_NAME)
        assert span is not None
        if bool(_escape_hatches(source, span, patterns)) is not want:
            print(f"self-test FAIL: the pragma scan misjudged {label}")
            return 1
    print("self-test PASS: comparator and pragma scan discriminate")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()

    source = (ENGINE_DIR / TARGET_PATH).read_text(encoding="utf-8")
    span = _function_span(source, FUNCTION_NAME)
    if span is None:
        print(f"FAIL: {FUNCTION_NAME} is not defined in {TARGET_PATH}", file=sys.stderr)
        return 1
    hatches = _escape_hatches(source, span, _coverage_patterns())
    if hatches:
        print(
            f"FAIL: {FUNCTION_NAME} carries a coverage.py escape hatch, which would hide an untested "
            "branch: " + "; ".join(hatches),
            file=sys.stderr,
        )
        return 1

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
                "--cov=agentce.commands",
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
    ok, message = _judge(file_data["functions"])
    print(message, file=sys.stderr if not ok else sys.stdout)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
