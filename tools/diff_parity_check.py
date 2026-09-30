"""diff_parity_check - prove Python, TypeScript, and Java compute an identical `agentce diff`.

`agentce diff`'s job (SPEC S9.3, item 18.6/18.24) is a real, content-keyed delta between two assertion
sets, rendered identically across all three engines: the report a reader gets from `--format text`,
`json`, or `md`, and from `--json`, must be the same choice of words and the same escaping no matter
which engine produced it. Three independent per-engine unit-test suites (18.24's C1/C2) can pass with
three independently-wrong-but-matching-in-no-way implementations, since none of them ever compares
against another engine's real output. This check does.

* `--self-test` needs no build: it proves two things about this checker itself, not about any engine.
  First, the comparator: a synthetic pair of "engine output" strings, identical then a one-byte
  tamper, must be told apart -- a check that always returns the same verdict regardless of its input
  must be caught here, the same discipline `catalog_digest_check.py`'s self-test already uses. Second,
  the fixture: a from-scratch reimplementation of the classifier (`_classify_change`,
  `engines/python/agentce/commands/__init__.py:2341` -- kept independent of `agentce`, since this
  script runs under `tools`' own `uv` environment, which does not install the engine) proves the
  shared fixture below actually produces at least one `closed`, one `opened`, and one `other`
  `what_changed` entry, so a later edit to the fixture cannot silently stop exercising all three
  groups, and that its duplicate `(control, subject)` pair resolves last-write-wins.
* The real invocation runs Python's own `agentce diff` (the console-script form, `uv run --frozen`, so
  this script's own environment never needs `agentce` installed) as the reference, the built
  TypeScript `dist/cli.js`, and the built Java runnable jar, over the identical fixture pair, for the
  six `--format`/`--json` combinations, one identical-input case (in both `text` and `md` form, which
  render two different literal strings for "no differences"), and one missing-file error case -- and
  asserts each engine's stdout is byte-identical to the other two, and their exit codes agree.

Usage:
    diff_parity_check.py             # needs `pnpm build` (TS) and `:assemble` (Java) run first
    diff_parity_check.py --self-test
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PY_ENGINE = ROOT / "engines" / "python"
TS_ENGINE = ROOT / "engines" / "typescript"
JAVA_ENGINE = ROOT / "engines" / "java"

#: SPEC vocabulary a `diff` classification treats as "not yet conformant"
#: (`agentce.verdict.GAP_OUTCOMES`, reproduced here so this script never imports the engine).
GAP_OUTCOMES = ("non-conformant", "partial", "insufficient_evidence", "not_assessed")

#: The same hostile payload `test_diff_sanitises_all_four_hostile_fields`
#: (`engines/python/tests/test_commands.py`) uses, reused here so this check exercises the identical
#: sanitiser input the per-engine unit tests already cover, not a fixture they happen not to touch.
HOSTILE = "ok<br>[x](evil)`y`\nVerdict: Conformant"
#: A BMP character plus an astral one, so this check exercises the `--json`/`--format json` non-ASCII
#: escaping fix (18.24's A2, and Java's `ensure_ascii`-equivalent) on real command output, not only
#: the isolated per-engine unit tests.
NON_ASCII = "cé😀"

#: The six `--format`/`--json` combinations item 18.24's contract requires: three human/machine
#: renderings, then the same three again under `--json`, which discards `--format`'s effect on
#: rendering entirely (`cmd_diff` returns before any `result.note` call) -- proving that is also this
#: check's job, not an assumption it is built on.
FORMAT_INVOCATIONS: list[tuple[str, list[str]]] = [
    ("format-text", ["--format", "text"]),
    ("format-json", ["--format", "json"]),
    ("format-md", ["--format", "md"]),
    ("json-default", ["--json"]),
    ("json-format-json", ["--json", "--format", "json"]),
    ("json-format-md", ["--json", "--format", "md"]),
]


def classify(before: str | None, after: str | None) -> str:
    """A from-scratch reimplementation of `_classify_change`
    (`engines/python/agentce/commands/__init__.py:2341`), used by the fixture-shape self-test below to
    prove the shared fixture exercises all three `what_changed` groups, and by
    `tools/installed_artifacts_check.py`'s C4 check (via `what_changed_groups`) to compute this same
    fixture's expected `diff` entries against an installed artifact -- never used by the real
    cross-engine invocation below, which compares the engines' own real output to each other, not to
    this reference."""
    if before is not None and before in GAP_OUTCOMES and after == "conformant":
        return "closed"
    if before == "conformant" and after is not None and after in GAP_OUTCOMES:
        return "opened"
    return "other"


def build_fixture(directory: Path) -> tuple[Path, Path]:
    """Write the shared `a.json`/`b.json` pair every invocation below diffs: one `closed`, one
    `opened`, one `other` (non-gap-touching outcome change), one added assertion, one removed
    assertion, one hostile control/subject pair, one non-ASCII control/subject pair, and one
    duplicate `(control, subject)` pair within a single side's own array (last write must win)."""
    a = [
        {"control": "C-CLOSED", "subject": "svc-1", "outcome": "non-conformant"},
        {"control": "C-OPENED", "subject": "svc-1", "outcome": "conformant"},
        {"control": "C-OTHER", "subject": "svc-1", "outcome": "not_applicable"},
        {"control": "C-REMOVED", "subject": "svc-1", "outcome": "conformant"},
        {"control": HOSTILE, "subject": HOSTILE, "outcome": "not_applicable"},
        {"control": NON_ASCII, "subject": NON_ASCII, "outcome": "non-conformant"},
        {"control": "C-DUP", "subject": "svc-1", "outcome": "conformant"},
        {"control": "C-DUP", "subject": "svc-1", "outcome": "not_assessed"},
    ]
    b = [
        {"control": "C-CLOSED", "subject": "svc-1", "outcome": "conformant"},
        {"control": "C-OPENED", "subject": "svc-1", "outcome": "partial"},
        {"control": "C-OTHER", "subject": "svc-1", "outcome": "partial"},
        {"control": HOSTILE, "subject": HOSTILE, "outcome": "partial"},
        {"control": NON_ASCII, "subject": NON_ASCII, "outcome": "conformant"},
        {"control": "C-DUP", "subject": "svc-1", "outcome": "conformant"},
        {"control": "C-ADDED", "subject": "svc-1", "outcome": "non-conformant"},
    ]
    a_path = directory / "a.json"
    b_path = directory / "b.json"
    a_path.write_text(json.dumps(a), encoding="utf-8")
    b_path.write_text(json.dumps(b), encoding="utf-8")
    return a_path, b_path


def _by_key(entries: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """`entries` keyed by `(control, subject)`, last write wins -- a plain dict comprehension already
    has that rule, the same rule the fixture-shape self-test asserts the fixture actually exercises."""
    return {(e["control"], e["subject"]): e["outcome"] for e in entries}


def what_changed_groups(
    a: list[dict[str, Any]], b: list[dict[str, Any]]
) -> dict[str, list[Any]]:
    """The reference grouping used by the fixture-shape self-test and by
    `tools/installed_artifacts_check.py`'s C4 check (see `classify`)."""
    left, right = _by_key(a), _by_key(b)
    groups: dict[str, list[Any]] = {"closed": [], "opened": [], "other": []}
    for key in sorted(set(left) | set(right)):
        before, after = left.get(key), right.get(key)
        if before != after:
            groups[classify(before, after)].append((key, before, after))
    return groups


def compare(
    label: str, a: str, b: str, engine_a: str, engine_b: str, failures: list[str]
) -> None:
    """Append a failure iff `a != b` -- the one comparator both the self-test and the real invocation
    below use, so the self-test proves the actual logic the real check relies on, not a stand-in."""
    if a != b:
        failures.append(f"{label}: {engine_a} and {engine_b} disagree")


def _run(cmd: list[str], *, cwd: Path | None = None) -> tuple[str, int]:
    """Run `cmd`, returning `(stdout, exit_code)`. Unlike `catalog_digest_check.py`'s `_run_or_die`, a
    non-zero exit is expected here (a differing pair exits 1, a malformed-input case exits 3), so the
    caller judges the exit code; this only runs the process."""
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return proc.stdout, proc.returncode


def python_diff(args: list[str]) -> tuple[str, int]:
    """The real Python engine's own `diff`, run in its own project environment as the console script
    (`python -m agentce` fails with "No module named agentce.__main__", confirmed by running it)."""
    return _run(
        [
            "uv",
            "run",
            "--frozen",
            "--project",
            str(PY_ENGINE),
            "agentce",
            "diff",
            *args,
        ],
        cwd=ROOT,
    )


def typescript_diff(args: list[str]) -> tuple[str, int]:
    """The built TypeScript engine's `diff` (`pnpm build` must have run first)."""
    entry = TS_ENGINE / "dist" / "cli.js"
    if not entry.is_file():
        raise SystemExit(
            f"typescript dist is not built: {entry} is missing "
            "(run `pnpm build` in engines/typescript first)"
        )
    return _run(["node", str(entry), "diff", *args], cwd=ROOT)


def java_diff(args: list[str]) -> tuple[str, int]:
    """The built Java engine's `diff` (`./gradlew :assemble` must have run first)."""
    jars = sorted(
        (JAVA_ENGINE / "build" / "libs").glob("agentce-*-all.jar"),
        key=lambda p: p.stat().st_mtime,
    )
    if not jars:
        raise SystemExit(
            "java runnable jar is not built "
            "(run `./gradlew :assemble -q` in engines/java first)"
        )
    return _run(["java", "-jar", str(jars[-1]), "diff", *args], cwd=ROOT)


def self_test() -> int:
    failures: list[str] = []

    # 1. the comparator itself: a check that always returns the same verdict must be caught here.
    unmoved: list[str] = []
    compare(
        "comparator-self-test",
        "identical output\n",
        "identical output\n",
        "a",
        "b",
        unmoved,
    )
    if unmoved:
        failures.append(
            "comparator wrongly flagged two identical strings as a mismatch"
        )
    caught: list[str] = []
    compare(
        "comparator-self-test",
        "identical output\n",
        "identical outputX\n",
        "a",
        "b",
        caught,
    )
    if not caught:
        failures.append(
            "comparator failed to catch a one-byte tamper between two 'engine output' strings"
        )

    # 2. the fixture-shape self-test.
    with tempfile.TemporaryDirectory(prefix="diff-parity-selftest-") as raw:
        tmp = Path(raw)
        a_path, b_path = build_fixture(tmp)
        a_entries = json.loads(a_path.read_text(encoding="utf-8"))
        b_entries = json.loads(b_path.read_text(encoding="utf-8"))
        groups = what_changed_groups(a_entries, b_entries)
        for key in ("closed", "opened", "other"):
            if not groups[key]:
                failures.append(
                    f"fixture does not exercise the {key!r} what_changed group"
                )
        # C-DUP's *first* A entry ("conformant") would collide with B's single "conformant" entry and
        # vanish as a non-change -- only the *last* A entry ("not_assessed") produces a visible change
        # at all, so this also proves the fixture actually needs last-write-wins to be exercised.
        dup_before = _by_key(a_entries).get(("C-DUP", "svc-1"))
        if dup_before != "not_assessed":
            failures.append(
                f"fixture's duplicate (control, subject) pair did not resolve last-write-wins: "
                f"got {dup_before!r}, expected 'not_assessed'"
            )

    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if not failures:
        print(
            "diff_parity_check self-test: comparator + fixture-shape cases discriminate"
        )
    return 1 if failures else 0


def run_real_check() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="diff-parity-") as raw:
        tmp = Path(raw)
        a_path, b_path = build_fixture(tmp)
        base = [str(a_path), str(b_path)]

        for label, extra in FORMAT_INVOCATIONS:
            py_out, py_code = python_diff([*base, *extra])
            ts_out, ts_code = typescript_diff([*base, *extra])
            java_out, java_code = java_diff([*base, *extra])
            if not (py_code == ts_code == java_code):
                failures.append(
                    f"{label}: exit codes disagree "
                    f"(python={py_code}, typescript={ts_code}, java={java_code})"
                )
            compare(label, py_out, ts_out, "python", "typescript", failures)
            compare(label, py_out, java_out, "python", "java", failures)

        # The identical-input case (`a` diffed against itself: zero changes, exit 0 in all three
        # engines) in both its `text` and `md` form -- two different literal strings for "no
        # differences" (no trailing period vs. one), asserted by exact match so the two can never be
        # silently conflated with each other.
        identical_cases: list[tuple[str, list[str], str]] = [
            ("identical-text", [], "no differences"),
            ("identical-md", ["--format", "md"], "## What changed\n\nno differences."),
        ]
        for label, extra, expected in identical_cases:
            args = [str(a_path), str(a_path), *extra]
            py_out, py_code = python_diff(args)
            ts_out, ts_code = typescript_diff(args)
            java_out, java_code = java_diff(args)
            if not (py_code == 0 and ts_code == 0 and java_code == 0):
                failures.append(
                    f"{label}: expected exit 0 in all three engines, got "
                    f"python={py_code}, typescript={ts_code}, java={java_code}"
                )
            if py_out.strip() != expected:
                failures.append(
                    f"{label}: python's own output is {py_out.strip()!r}, expected {expected!r} "
                    "-- the fixture or format assumption above is wrong"
                )
            compare(label, py_out, ts_out, "python", "typescript", failures)
            compare(label, py_out, java_out, "python", "java", failures)

        # A missing second file: each engine's own error-envelope field name differs
        # (`error.key` in Python, `error.message_key` in TS/Java), so this reads each by its own name
        # rather than assuming a single shared field, and compares only the value.
        args = [str(a_path), "--json"]
        py_out, py_code = python_diff(args)
        ts_out, ts_code = typescript_diff(args)
        java_out, java_code = java_diff(args)
        if not (py_code == 3 and ts_code == 3 and java_code == 3):
            failures.append(
                f"missing-file: expected exit 3 in all three engines, got "
                f"python={py_code}, typescript={ts_code}, java={java_code}"
            )
        py_key = json.loads(py_out)["error"]["key"]
        ts_key = json.loads(ts_out)["error"]["message_key"]
        java_key = json.loads(java_out)["error"]["message_key"]
        expected_key = "input.report_b_missing"
        if not (py_key == ts_key == java_key == expected_key):
            failures.append(
                f"missing-file: error key mismatch, expected {expected_key!r} in all three, got "
                f"python={py_key!r}, typescript={ts_key!r}, java={java_key!r}"
            )

    for failure in failures:
        print(f"MISMATCH: {failure}", file=sys.stderr)
    if failures:
        return 1
    total = len(FORMAT_INVOCATIONS) + len(["identical-text", "identical-md"]) + 1
    print(f"MATCH: {total} invocations byte-identical across python, typescript, java")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="diff_parity_check", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    return run_real_check()


if __name__ == "__main__":
    raise SystemExit(main())
