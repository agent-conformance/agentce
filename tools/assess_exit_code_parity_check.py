"""assess_exit_code_parity_check - prove Python, TypeScript, and Java agree on `assess`'s exit code
(SPEC.md:1076, item 18.30: exit code 2, insufficient evidence on any `severity: high` control).

Three independent per-engine unit-test suites can each pass while the exit-2 wiring diverges across
engines (one computing it from a differently-scoped `evaluated` list, say) since none of them ever runs
another engine's own binary and compares. This check does, over four real corpus projects with known,
hand-verified expected codes (captured live against all three engines while building this item -- see
`harness/remediation/journal/18.30.md`):

* `credit/langgraph/insufficient-evidence` -> 2 (`exit_status` containing both `findings` and
  `insufficient_evidence`: OVS-03 is `severity: high` and forced to `insufficient_evidence` by this
  corpus variant, and 7 unrelated controls are genuinely non-conformant, so both codes apply at once).
* `credit/langgraph/known-pass` -> 0, `credit/langgraph/known-fail` -> 1 (neither has a severity-high
  `insufficient_evidence` assertion, so exit 2 must never fire for either).
* the vendored `corpus/quickstart` -> 0 (19 `insufficient_evidence` assertions, all `severity: medium`,
  proving the check is scoped to severity rather than the outcome alone).

* `--self-test` needs no build: it proves the comparator (a synthetic pair of "engine output" strings,
  identical then a one-byte tamper, must be told apart), the same discipline `diff_parity_check.py`'s
  own self-test uses.
* `--engine <name>` runs one engine alone against the expected-code table (C1's single-engine mode,
  reference = Python, no build required for the other two).
* The default (no `--engine`) mode builds and runs all three engines and asserts their exit codes (and,
  for `insufficient-evidence`, their full `exit_status` arrays) agree with each other, not just with the
  expected-code table (C3's three-engine parity, SPEC §11.5 discipline applied to exit codes).

Usage:
    assess_exit_code_parity_check.py                  # needs `pnpm build` (TS) and `:assemble` (Java)
    assess_exit_code_parity_check.py --engine python   # Python only, no build required
    assess_exit_code_parity_check.py --self-test
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_ENGINE = ROOT / "engines" / "python"
TS_ENGINE = ROOT / "engines" / "typescript"
JAVA_ENGINE = ROOT / "engines" / "java"
QUICKSTART = ROOT / "corpus" / "quickstart"

#: Where C1/C3's own `generate.py --out` commands write the generated corpus (hardcoded, matching the
#: contract's own C1/C3 commands).
CORPUS_ROOT = Path("/tmp/vg-exit-code-corpus")
LANGGRAPH = CORPUS_ROOT / "projects" / "credit" / "langgraph"

#: label -> (bundle, profile, domain, expected exit code, expected exit_status). `exit_status` is
#: asserted only where the contract names it explicitly (insufficient-evidence's two-codes-at-once
#: case); the other three projects assert the exit code alone.
PROJECTS: list[tuple[str, Path, int, list[str] | None]] = [
    (
        "insufficient-evidence",
        LANGGRAPH / "insufficient-evidence",
        2,
        ["findings", "insufficient_evidence"],
    ),
    ("known-pass", LANGGRAPH / "known-pass", 0, None),
    ("known-fail", LANGGRAPH / "known-fail", 1, None),
    ("quickstart", QUICKSTART, 0, None),
]


def _bundle_args(project: Path) -> list[str]:
    return [
        "--bundle",
        str(project / "evidence"),
        "--profile",
        str(project / "applicability.yaml"),
        "--domain",
        str(project / "domain.linkml.yaml"),
    ]


def _run(cmd: list[str], *, cwd: Path | None = None) -> tuple[str, int]:
    """Run `cmd`, returning `(stdout, exit_code)`; a non-zero exit (1, 2, or 3) is expected, so the
    caller judges it, mirroring `diff_parity_check.py`'s own `_run`."""
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return proc.stdout, proc.returncode


def python_assess(project: Path, out: Path) -> tuple[str, int]:
    """The real Python engine's own `assess`, run in its own project environment as the console script
    (matching `diff_parity_check.py`'s `python_diff`)."""
    return _run(
        [
            "uv",
            "run",
            "--frozen",
            "--project",
            str(PY_ENGINE),
            "agentce",
            "assess",
            *_bundle_args(project),
            "--out",
            str(out),
            "--json",
        ],
        cwd=ROOT,
    )


def typescript_assess(project: Path, out: Path) -> tuple[str, int]:
    """The built TypeScript engine's `assess` (`pnpm build` must have run first)."""
    entry = TS_ENGINE / "dist" / "cli.js"
    if not entry.is_file():
        raise SystemExit(
            f"typescript dist is not built: {entry} is missing "
            "(run `pnpm build` in engines/typescript first)"
        )
    return _run(
        [
            "node",
            str(entry),
            "assess",
            *_bundle_args(project),
            "--out",
            str(out),
            "--json",
        ],
        cwd=ROOT,
    )


def java_assess(project: Path, out: Path) -> tuple[str, int]:
    """The built Java engine's `assess` (`./gradlew :assemble` must have run first)."""
    jars = sorted(
        (JAVA_ENGINE / "build" / "libs").glob("agentce-*-all.jar"),
        key=lambda p: p.stat().st_mtime,
    )
    if not jars:
        raise SystemExit(
            "java runnable jar is not built "
            "(run `./gradlew :assemble -q` in engines/java first)"
        )
    return _run(
        [
            "java",
            "-jar",
            str(jars[-1]),
            "assess",
            *_bundle_args(project),
            "--out",
            str(out),
            "--json",
        ],
        cwd=ROOT,
    )


ENGINES = {
    "python": python_assess,
    "typescript": typescript_assess,
    "java": java_assess,
}


def compare(
    label: str, a: str, b: str, engine_a: str, engine_b: str, failures: list[str]
) -> None:
    """Append a failure iff `a != b` -- the one comparator both the self-test and the real invocation
    below use (same discipline as `diff_parity_check.py`'s `compare`)."""
    if a != b:
        failures.append(f"{label}: {engine_a} and {engine_b} disagree ({a!r} vs {b!r})")


def self_test() -> int:
    failures: list[str] = []

    unmoved: list[str] = []
    compare("comparator-self-test", "2", "2", "a", "b", unmoved)
    if unmoved:
        failures.append("comparator wrongly flagged two identical values as a mismatch")
    caught: list[str] = []
    compare("comparator-self-test", "2", "1", "a", "b", caught)
    if not caught:
        failures.append(
            "comparator failed to catch a one-value tamper between two 'engine output' codes"
        )

    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if not failures:
        print("assess_exit_code_parity_check self-test: comparator discriminates")
    return 1 if failures else 0


def run_single_engine(name: str) -> int:
    assess = ENGINES[name]
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="assess-exit-code-parity-") as raw:
        tmp = Path(raw)
        for label, project, expected_code, expected_status in PROJECTS:
            out, code = assess(project, tmp / label)
            if code != expected_code:
                failures.append(
                    f"{label}: {name} exited {code}, expected {expected_code}"
                )
                continue
            if expected_status is not None:
                envelope = json.loads(out)
                if envelope.get("exit_status") != expected_status:
                    failures.append(
                        f"{label}: {name} exit_status is {envelope.get('exit_status')!r}, "
                        f"expected {expected_status!r}"
                    )
    for failure in failures:
        print(f"MISMATCH: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(
        f"MATCH: {name}'s assess agrees with the expected-code table over {len(PROJECTS)} projects"
    )
    return 0


def run_three_engine_check() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="assess-exit-code-parity-") as raw:
        tmp = Path(raw)
        for label, project, expected_code, expected_status in PROJECTS:
            results = {
                name: assess(project, tmp / f"{label}-{name}")
                for name, assess in ENGINES.items()
            }
            codes = {name: code for name, (_out, code) in results.items()}
            if not all(code == expected_code for code in codes.values()):
                failures.append(
                    f"{label}: expected exit {expected_code} in all three engines, got "
                    + ", ".join(
                        f"{name}={code}" for name, code in sorted(codes.items())
                    )
                )

            if expected_status is not None:
                statuses = {
                    name: json.loads(out).get("exit_status")
                    for name, (out, _code) in results.items()
                }
                reference = statuses["python"]
                for name in ENGINES:
                    if name == "python":
                        continue
                    compare(
                        f"{label}-exit_status",
                        json.dumps(reference),
                        json.dumps(statuses[name]),
                        "python",
                        name,
                        failures,
                    )
                if reference != expected_status:
                    failures.append(
                        f"{label}: python's own exit_status is {reference!r}, expected {expected_status!r} "
                        "-- the expected-code table above is wrong"
                    )

    for failure in failures:
        print(f"MISMATCH: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(
        f"MATCH: {len(PROJECTS)} projects, identical assess exit codes across python, typescript, java"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="assess_exit_code_parity_check", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--engine", choices=sorted(ENGINES), default=None)
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.engine is not None:
        return run_single_engine(args.engine)
    return run_three_engine_check()


if __name__ == "__main__":
    raise SystemExit(main())
