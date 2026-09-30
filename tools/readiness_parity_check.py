"""readiness_parity_check - prove Python, TypeScript, and Java compute an identical `agentce
readiness`.

`agentce readiness`'s job (SPEC S13.3.4, item 18.25) is a report-readiness verdict (READY / READY WITH
LIMITATIONS / NOT READY) and the deviation-register linter it applies when a register is given,
byte-identical across all three engines: the `--json` envelope and the generated
`report-readiness-*.md` file must be the same choice of words no matter which engine produced them.
Three independent per-engine unit-test suites (18.25's C1/C2) can pass with three
independently-wrong-but-matching-in-no-way implementations, since none of them ever compares against
another engine's real output. This check does. Mirrors `diff_parity_check.py`'s shape (18.24's C3).

* `--self-test` needs no build: it proves two things about this checker itself, not about any engine.
  First, the comparator: a synthetic pair of "engine output" strings, identical then a one-byte
  tamper, must be told apart. Second, the fixture: a from-scratch reimplementation of
  `compute_readiness` (`_expected_verdict`, kept independent of `agentce`, since this script runs
  under `tools`' own `uv` environment, which does not install the engine) proves each of the five
  named scenarios below actually produces the verdict/reasons/limitations shape it claims, so a later
  edit to a fixture cannot silently stop exercising the case it is named for.
* The real invocation runs Python's own `agentce readiness` (the console-script form, `uv run
  --frozen`, so this script's own environment never needs `agentce` installed) as the reference, the
  built TypeScript `dist/cli.js`, and the built Java runnable jar, over each of the five scenarios --
  asserting the `--json` envelope's `verdict`/`reasons`/`limitations` fields and the generated
  `report-readiness-*.md` file's content (both date-stripped first, since the file name and its first
  line embed the wall-clock date) are byte-identical across all three engines, and their exit codes
  agree.

Usage:
    readiness_parity_check.py             # needs `pnpm build` (TS) and `:assemble` (Java) run first
    readiness_parity_check.py --self-test
"""

from __future__ import annotations

import argparse
import functools
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PY_ENGINE = ROOT / "engines" / "python"
TS_ENGINE = ROOT / "engines" / "typescript"
JAVA_ENGINE = ROOT / "engines" / "java"

READY = "READY"
READY_WITH_LIMITATIONS = "READY WITH LIMITATIONS"
NOT_READY = "NOT READY"

#: The real, vendored `eu-ai-act` base catalog -- the same fixture `readiness.test.ts`/`ReadinessTest`'s
#: CLI-wiring tests already use (`OVS-03` is `severity: high`, `DAT-01` is `severity: medium`). Building
#: a synthetic catalog from scratch is out of scope here: a `catalog.yaml` references one file per
#: control, each with its own schema and a provenance digest the loader verifies.
CATALOG_DIR = ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"

#: Strips a `report-readiness-<ISO date>.md` file name, and a Markdown/`--json` embedded ISO date, so
#: the byte-compare below never fails on the wall-clock date alone -- an accepted limitation this check
#: does not try to close, mirroring `diff_parity_check.py`'s own scope.
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _strip_dates(text: str) -> str:
    return DATE_RE.sub("<DATE>", text)


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text("".join(f"{json.dumps(r)}\n" for r in records), encoding="utf-8")


def _report_dir(
    directory: Path,
    name: str,
    *,
    assertions: list[dict[str, Any]] | None = None,
    integrity: list[dict[str, Any]] | None = None,
) -> Path:
    report = directory / name
    report.mkdir()
    (report / "assertions.json").write_text(
        json.dumps(assertions or []), encoding="utf-8"
    )
    _write_jsonl(report / "integrity.jsonl", integrity or [])
    (report / "coverage.json").write_text(
        json.dumps({"subjects": {}}), encoding="utf-8"
    )
    (report / "applicability.jsonl").write_text("", encoding="utf-8")
    return report


class Scenario:
    """One of the five named report-readiness fixtures this check pins (contract C3). `build` writes
    the report directory (and any `--gaps`/`--deviations` file) under `directory` and returns the CLI
    args (after the report-dir positional) every engine is run with; `expect` is this checker's own
    from-scratch reimplementation of the expected verdict shape, used only by the self-test."""

    def __init__(
        self,
        name: str,
        build: Any,
        expect_verdict: str,
        expect_exit: int,
    ) -> None:
        self.name = name
        self.build = build
        self.expect_verdict = expect_verdict
        self.expect_exit = expect_exit


def _scenario_1_clean(directory: Path) -> tuple[Path, list[str]]:
    """(1) clean -- READY."""
    report = _report_dir(
        directory,
        "s1",
        assertions=[{"control": "OVS-03", "outcome": "conformant", "subject": "s"}],
        integrity=[{"status": "verified", "stream": "a"}],
    )
    return report, []


def _scenario_2_regression(directory: Path) -> tuple[Path, list[str]]:
    """(2) the round-1/round-2 regression set, folded into one scenario: two `failed` integrity
    records naming the same stream from two different subjects (the identical reason string must
    appear twice), a third naming a fullwidth/non-BMP-but-still-BMP stream (U+FF21, which agrees with
    a naive UTF-16 sort and so does not by itself distinguish it from `byteCompare` -- verifier
    round-1 finding), a fourth naming an *astral* stream (U+1F600, a UTF-16 surrogate pair that a
    naive UTF-16 sort places before U+FF21 while `byteCompare`'s UTF-8 bytes place it after -- the
    case that actually exercises the divergence), and a fifth with no `status`/`stream` field at all
    (`None` must render in its reason text) -- NOT READY."""
    report = _report_dir(
        directory,
        "s2",
        integrity=[
            {"status": "failed", "stream": "gw"},
            {"status": "failed", "stream": "gw"},
            {"status": "failed", "stream": "Ａ"},
            {"status": "failed", "stream": "😀"},
            {"status": "gap"},
        ],
    )
    return report, []


def _scenario_3_no_gaps(directory: Path) -> tuple[Path, list[str]]:
    """(3) a high-severity insufficient_evidence assertion with no --gaps -- NOT READY."""
    report = _report_dir(
        directory,
        "s3",
        assertions=[
            {"control": "OVS-03", "outcome": "insufficient_evidence", "subject": "s"}
        ],
    )
    return report, []


def _scenario_4_gaps(directory: Path) -> tuple[Path, list[str]]:
    """(4) the same assertion with a --gaps file naming the control -- READY WITH LIMITATIONS."""
    report = _report_dir(
        directory,
        "s4",
        assertions=[
            {"control": "OVS-03", "outcome": "insufficient_evidence", "subject": "s"}
        ],
    )
    gaps = directory / "s4-gaps.md"
    gaps.write_text("OVS-03 owned by alice on 2026-02-01\n", encoding="utf-8")
    return report, ["--gaps", str(gaps)]


def _scenario_5_deviations(directory: Path) -> tuple[Path, list[str]]:
    """(5) a deviation register with two entries: one missing `approver` (refused, forcing NOT_READY
    regardless of the second entry) and one applied (its control's only assertion has
    outcome: partial, deviation: true) whose unquoted YAML date-time `expiry` is before the run's
    `asOf` (a later window.end) -- the one deviation_lint code path that prints a date -- NOT READY."""
    report = _report_dir(
        directory,
        "s5",
        assertions=[
            {
                "control": "OVS-03",
                "outcome": "partial",
                "subject": "s",
                "deviation": "OVS-03",
                "window": {"start": "2026-01-01", "end": "2026-02-01"},
            },
            {
                "control": "DAT-01",
                "outcome": "non-conformant",
                "subject": "s",
            },
        ],
    )
    deviations = directory / "s5-deviations.yaml"
    deviations.write_text(
        "\n".join(
            [
                "deviations:",
                "  - control: OVS-03",
                "    rationale: compensated",
                "    compensating_control: manual review",
                "    owner: alice",
                "    approver: bob",
                "    granted: 2019-08-01",
                # Unquoted, so every engine resolves it as a timestamp and pythonizes it.
                "    expiry: 2020-01-01T10:00:00Z",
                "  - control: DAT-01",
                "    rationale: compensated",
                "    compensating_control: manual review",
                "    owner: alice",
                "    granted: 2026-01-01",
                "    expiry: 2026-03-01",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return report, ["--deviations", str(deviations)]


SCENARIOS: list[Scenario] = [
    Scenario("1-clean", _scenario_1_clean, READY, 0),
    Scenario("2-regression", _scenario_2_regression, NOT_READY, 1),
    Scenario("3-insufficient-evidence-no-gaps", _scenario_3_no_gaps, NOT_READY, 1),
    Scenario(
        "4-insufficient-evidence-with-gaps", _scenario_4_gaps, READY_WITH_LIMITATIONS, 0
    ),
    Scenario("5-deviations", _scenario_5_deviations, NOT_READY, 1),
]


def _argv_vectors(directory: Path) -> list[tuple[str, list[str], int]]:
    """Argv shapes argparse reads in a particular way (18.25 round 3): each is (name, argv, the exit
    code Python's `readiness` subparser gives). Over scenario 3's report (a high-severity control with
    insufficient evidence), a gaps file naming it gives READY WITH LIMITATIONS (0) and an empty one
    NOT READY (1), so a repeated flag shows which value an engine kept. Exit 3 is a refusal: Python's
    is argparse's own usage error, TS/Java's the keyed `input.readiness_unrecognized_flag`, so only
    the exit code is compared there."""
    directory = directory / "argv"
    directory.mkdir()
    report, _ = _scenario_3_no_gaps(directory)
    named = directory / "v-gaps.md"
    named.write_text("OVS-03 owned by alice on 2026-02-01\n", encoding="utf-8")
    empty = directory / "v-empty-gaps.md"
    empty.write_text("", encoding="utf-8")
    r, g, e, c = str(report), str(named), str(empty), str(CATALOG_DIR)
    return [
        (
            "repeated-flag-last-wins-limitations",
            [r, "--gaps", e, "--gaps", g, "--catalog-dir", c],
            0,
        ),
        (
            "repeated-flag-last-wins-not-ready",
            [r, "--gaps", g, "--gaps", e, "--catalog-dir", c],
            1,
        ),
        ("equals-joined-value", [r, f"--gaps={g}", f"--catalog-dir={c}"], 0),
        ("empty-value-ignored", [r, "--gaps", "", "--catalog-dir", c], 1),
        ("options-end-marker", ["--gaps", g, "--catalog-dir", c, "--", r], 0),
        ("abbreviated-flag", [r, "--gap", g, "--catalog-dir", c], 3),
        ("abbreviated-global-flag", [r, "--gaps", g, "--catalog-dir", c, "--js"], 3),
        ("flag-without-value-at-end", [r, "--catalog-dir", c, "--gaps"], 3),
        ("flag-followed-by-a-flag", [r, "--gaps", "--quiet", "--catalog-dir", c], 3),
        ("boolean-flag-with-value", [r, "--catalog-dir", c, "--quiet=1"], 3),
        ("second-positional", [r, r, "--catalog-dir", c], 3),
        ("single-dash-unknown", [r, "-x", "--catalog-dir", c], 3),
    ]


def compare(
    label: str, a: str, b: str, engine_a: str, engine_b: str, failures: list[str]
) -> None:
    """Append a failure iff `a != b` -- the one comparator both the self-test and the real invocation
    below use, so the self-test proves the actual logic the real check relies on, not a stand-in."""
    if a != b:
        failures.append(f"{label}: {engine_a} and {engine_b} disagree")


def compare_ports(label: str, outputs: list[str], failures: list[str]) -> None:
    """`compare` of the Python reference (`outputs[0]`) against each port (TypeScript, Java)."""
    py, ts, java = outputs
    compare(label, py, ts, "python", "typescript", failures)
    compare(label, py, java, "python", "java", failures)


def _run(cmd: list[str], *, cwd: Path | None = None) -> tuple[str, int]:
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return proc.stdout, proc.returncode


def python_readiness(args: list[str]) -> tuple[str, int]:
    """The real Python engine's own `readiness`, run in its own project environment as the console
    script (`python -m agentce` fails with "No module named agentce.__main__", confirmed 18.24)."""
    return _run(
        [
            "uv",
            "run",
            "--frozen",
            "--project",
            str(PY_ENGINE),
            "agentce",
            "readiness",
            *args,
        ],
        cwd=ROOT,
    )


def typescript_readiness(args: list[str]) -> tuple[str, int]:
    entry = TS_ENGINE / "dist" / "cli.js"
    if not entry.is_file():
        raise SystemExit(
            f"typescript dist is not built: {entry} is missing "
            "(run `pnpm build` in engines/typescript first)"
        )
    return _run(["node", str(entry), "readiness", *args], cwd=ROOT)


@functools.cache
def _java_jar() -> Path:
    jars = sorted(
        (JAVA_ENGINE / "build" / "libs").glob("agentce-*-all.jar"),
        key=lambda p: p.stat().st_mtime,
    )
    if not jars:
        raise SystemExit(
            "java runnable jar is not built (run `./gradlew :assemble -q` in engines/java first)"
        )
    return jars[-1]


def java_readiness(args: list[str]) -> tuple[str, int]:
    return _run(["java", "-jar", str(_java_jar()), "readiness", *args], cwd=ROOT)


def self_test() -> int:
    failures: list[str] = []

    # 1. the comparator itself.
    unmoved: list[str] = []
    compare("comparator-self-test", "identical\n", "identical\n", "a", "b", unmoved)
    if unmoved:
        failures.append(
            "comparator wrongly flagged two identical strings as a mismatch"
        )
    caught: list[str] = []
    compare("comparator-self-test", "identical\n", "identicalX\n", "a", "b", caught)
    if not caught:
        failures.append(
            "comparator failed to catch a one-byte tamper between two 'engine output' strings"
        )

    # 1b. the report-read-order discipline (a verifier round found this vacuous once): all three
    # engines write the same `report-readiness-<date>.md` path inside one shared report dir, each
    # overwriting the last engine's file, so a read must happen immediately after that engine's own
    # run -- reading all three only after all three runs would silently compare the last writer's
    # file against itself under three different names. Simulates three sequential overwrites of one
    # shared file and confirms an immediate read after each write captures that write's own content,
    # distinct from what a deferred read (after every write) would return.
    with tempfile.TemporaryDirectory(prefix="readiness-parity-selftest-order-") as raw:
        shared = Path(raw) / "report-readiness-2026-01-01.md"
        contents = ["# python\n", "# typescript\n", "# java\n"]
        immediate: list[str] = []
        for content in contents:
            shared.write_text(content, encoding="utf-8")
            immediate.append(shared.read_text(encoding="utf-8"))
        if immediate != contents:
            failures.append(
                f"report-read-order self-test: an immediate read did not capture its own write "
                f"(got {immediate!r}, expected {contents!r})"
            )
        deferred = [shared.read_text(encoding="utf-8") for _ in contents]
        if deferred == immediate:
            failures.append(
                "report-read-order self-test: a deferred read (simulating the vacuous bug) matched "
                "the immediate reads -- this self-test no longer distinguishes the two read orders"
            )

    # 2. the fixture-shape self-test: each scenario's own from-scratch expectation actually holds.
    with tempfile.TemporaryDirectory(prefix="readiness-parity-selftest-") as raw:
        tmp = Path(raw)
        for scenario in SCENARIOS:
            report, extra = scenario.build(tmp)
            assertions = json.loads(
                (report / "assertions.json").read_text(encoding="utf-8")
            )
            integrity = [
                json.loads(line)
                for line in (report / "integrity.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            if scenario.name == "2-regression":
                reasons = [
                    f"integrity {r.get('status')} on stream {r.get('stream')}"
                    for r in integrity
                ]
                dupes = [r for r in reasons if r == "integrity failed on stream gw"]
                if len(dupes) != 2:
                    failures.append(
                        f"{scenario.name}: fixture does not produce the duplicate reason string twice "
                        f"(got {len(dupes)})"
                    )
                if "integrity gap on stream None" not in reasons:
                    failures.append(
                        f"{scenario.name}: fixture's missing-field record does not render 'None'"
                    )
                # The byteCompare-vs-naive-sort distinction itself is C1/C2's job (readiness.test.ts /
                # ReadinessTest.java already assert it); this scenario only needs the duplicate and the
                # missing-field record to be present so the real cross-engine invocation below actually
                # exercises both.
            if scenario.name == "5-deviations" and not any(
                a.get("deviation") for a in assertions
            ):
                failures.append(f"{scenario.name}: fixture has no applied deviation")
            if not (report / "assertions.json").is_file():
                failures.append(
                    f"{scenario.name}: fixture did not write assertions.json"
                )
            _ = extra  # only used by the real invocation below

    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if not failures:
        print(
            "readiness_parity_check self-test: comparator + fixture-shape cases discriminate"
        )
    return 1 if failures else 0


def run_real_check() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="readiness-parity-") as raw:
        tmp = Path(raw)
        for scenario in SCENARIOS:
            report, extra = scenario.build(tmp)
            args = [str(report), *extra, "--catalog-dir", str(CATALOG_DIR)]

            # All three engines write the same `report-readiness-<date>.md` path inside the shared
            # `report` dir, each overwriting the last engine's file. Reading a report's file must
            # therefore happen immediately after that engine's own run, before the next engine
            # overwrites it -- reading all three only after all three runs would silently compare the
            # last engine's file against itself under three different names (round-2 B1: the read
            # order, not just the shared path, made this comparison vacuous).
            py_out, py_code = python_readiness([*args, "--json"])
            py_env = json.loads(py_out)
            py_report = _strip_dates(Path(py_env["report"]).read_text(encoding="utf-8"))

            ts_out, ts_code = typescript_readiness([*args, "--json"])
            ts_env = json.loads(ts_out)
            ts_report = _strip_dates(Path(ts_env["report"]).read_text(encoding="utf-8"))

            java_out, java_code = java_readiness([*args, "--json"])
            java_env = json.loads(java_out)
            java_report = _strip_dates(
                Path(java_env["report"]).read_text(encoding="utf-8")
            )

            if not (py_code == scenario.expect_exit == ts_code == java_code):
                failures.append(
                    f"{scenario.name}: expected exit {scenario.expect_exit} in all three engines, got "
                    f"python={py_code}, typescript={ts_code}, java={java_code}"
                )

            for key in ("verdict", "reasons", "limitations"):
                # `json.dumps` on a plain string round-trips to the same bytes a direct compare would
                # use, so this one form covers both the scalar `verdict` and the list fields.
                compare_ports(
                    f"{scenario.name}:{key}",
                    [json.dumps(env.get(key)) for env in (py_env, ts_env, java_env)],
                    failures,
                )
            if py_env.get("verdict") != scenario.expect_verdict:
                failures.append(
                    f"{scenario.name}: python's own verdict is {py_env.get('verdict')!r}, expected "
                    f"{scenario.expect_verdict!r} -- the fixture assumption above is wrong"
                )

            compare_ports(
                f"{scenario.name}:report.md",
                [py_report, ts_report, java_report],
                failures,
            )

        vectors = _argv_vectors(tmp)
        for name, argv, expect in vectors:
            runs = [
                ("python", python_readiness(["--json", *argv])),
                ("typescript", typescript_readiness(["--json", *argv])),
                ("java", java_readiness(["--json", *argv])),
            ]
            codes = {engine: code for engine, (_, code) in runs}
            if set(codes.values()) != {expect}:
                failures.append(
                    f"argv:{name}: expected exit {expect} in all three engines, got {codes}"
                )
            elif expect != 3:
                verdicts = [
                    json.dumps(json.loads(out).get("verdict")) for _, (out, _) in runs
                ]
                compare_ports(f"argv:{name}:verdict", verdicts, failures)

    for failure in failures:
        print(f"MISMATCH: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(
        f"MATCH: {len(SCENARIOS)} scenarios byte-identical and {len(vectors)} argv shapes read alike "
        "across python, typescript, java"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="readiness_parity_check", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    return run_real_check()


if __name__ == "__main__":
    raise SystemExit(main())
