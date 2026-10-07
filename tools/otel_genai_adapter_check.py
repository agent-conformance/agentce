"""otel_genai_adapter_check - prove Python, TypeScript, and Java map OTLP/JSON GenAI and
OpenInference trace exports to an identical set of canonical AgentCE evidence events (SPEC 12.1,
item 18.29, contract `P18-18.29` C3).

Three independently-interpreted ports of the same adapter (18.29's C1/C2) can each pass their own
unit-test suite while silently disagreeing on an edge case none of those suites happens to probe.
This check never trusts that: it runs the identical 9 vendor fixtures
(`adapters/otel-genai/fixtures/`) and 21 hostile vectors
(`spec/model/test-vectors/otel-genai-hostile/`) -- 30 in total -- through all three engines and
asserts their `EVENT`/`REPORT`/`ERROR` output is byte-identical.

Python's own adapter (`agentce.records.otel_genai`, the engine's vendored copy C0 hardened) is
called directly, in process -- this script is meant to run under `engines/python`'s own `uv`
environment (`uv run --project engines/python --frozen python tools/otel_genai_adapter_check.py`),
exactly as `--self-test` and the real invocation's `cmd` in the contract pin. TypeScript and Java
are driven as built artifacts (`engines/typescript/dist/cli.js`, the Java runnable jar), each
through the shared `otel-genai-fixture <dir>` seam verb (`cli.ts`/`Cli.java`).

* `--self-test` needs no TypeScript/Java build: it proves two things about this checker itself, not
  about any engine. First, every one of the 30 vectors' own expected output
  (`expected.jsonl`/`expected-report.json`, or `expected-error.json` -- both plain `AdapterError`
  reasons and `canonical:<reason>` canonicalization refusals) matches what Python's own adapter
  actually produces, so a later edit to a vector's expected file cannot silently drift from the
  reference it is supposed to pin. Second, the comparator: a deliberately-broken stand-in built by
  seeding the exact two faults the `truncation-and-enums` vector exists to catch (rounding instead
  of truncating a sub-millisecond bare-number timestamp, and passing through an out-of-enum
  tool-protocol/memory-trust value a correct filter would drop) must be told apart from the real
  output -- proving the check design actually discriminates on this fault class, not only that the
  seam exists.
* The real invocation (the caller builds TypeScript's `dist/` and the Java runnable jar first) runs,
  for each of the 30 vectors, a fresh temporary directory holding only that vector's own
  `input.json` and `adapt.json` (never its `expected*.json*`, so a seam that merely echoes the
  directory it is given cannot pass) through Python's `adapt` directly, the built TypeScript
  `dist/cli.js otel-genai-fixture <dir>`, and the built Java jar's `otel-genai-fixture <dir>`, and
  asserts all three engines' stdout/stderr and exit code agree. Each TypeScript and Java run has a
  fixed 30 s limit; a run that exceeds it fails naming the vector and the engine (the
  `long-slash-run-schema-url` vector exists to catch a super-linear scan this way).

Usage:
    otel_genai_adapter_check.py             # needs `pnpm build` (TS) and `:assemble` (Java) run first
    otel_genai_adapter_check.py --self-test
"""

from __future__ import annotations

import argparse
import functools
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from agentce.canonical import CanonicalizationError, canonical_string
from agentce.records.otel_genai import AdapterError, adapt

ROOT = Path(__file__).resolve().parent.parent
TS_ENGINE = ROOT / "engines" / "typescript"
JAVA_ENGINE = ROOT / "engines" / "java"
FIXTURES_DIR = ROOT / "adapters" / "otel-genai" / "fixtures"
HOSTILE_DIR = ROOT / "spec" / "model" / "test-vectors" / "otel-genai-hostile"

#: The vendor fixtures plus hostile vectors this check exercises every engine against -- 9 + 21 = 30
#: (contract `P18-18.29` C3; 18.29 verifier round 1 added `empty-input` and `doubled-bom`,
#: round 2 `long-number-strings` and `integer-literal-too-long`; `long-slash-run-schema-url` pins
#: the linear schema-URL trailing-slash scan).
EXPECTED_VECTOR_COUNT = 30

#: The fixed limit on one TypeScript or Java run over one vector. Every vector runs in well under a
#: second; a run that exceeds this is a hang (a super-linear scan), reported as a TIMEOUT failure.
ENGINE_TIMEOUT_S = 30


def all_vector_dirs() -> list[Path]:
    """Every fixture/vector directory, sorted, from both the vendor set and the hostile set."""
    fixtures = sorted(
        p for p in FIXTURES_DIR.iterdir() if p.is_dir() and (p / "input.json").exists()
    )
    hostile = sorted(
        p for p in HOSTILE_DIR.iterdir() if p.is_dir() and (p / "input.json").exists()
    )
    return fixtures + hostile


def compare(
    label: str, a: str, b: str, engine_a: str, engine_b: str, failures: list[str]
) -> None:
    """Append a failure iff `a != b` -- the one comparator both the self-test and the real
    invocation use, so the self-test proves the actual logic the real check relies on."""
    if a != b:
        failures.append(f"{label}: {engine_a} and {engine_b} disagree")


# --- Python reference: called directly, in process. -------------------------------------------------


def _adapt_kwargs(vector_dir: Path) -> dict[str, object]:
    args = json.loads((vector_dir / "adapt.json").read_text(encoding="utf-8"))
    source_class = args.get("source_class")
    return {
        "subject": args.get("subject"),
        "source_class": source_class if source_class is not None else "self_report",
        "source": args.get("source"),
    }


def _report_json(report: object) -> dict[str, object]:
    return {
        "adapter": report.adapter,  # type: ignore[attr-defined]
        "conventions": list(report.conventions),  # type: ignore[attr-defined]
        "spans_seen": report.spans_seen,  # type: ignore[attr-defined]
        "events_emitted": report.events_emitted,  # type: ignore[attr-defined]
        "skipped": [
            {"name": s.name, "reason": s.reason, "span_id": s.span_id}
            for s in report.skipped  # type: ignore[attr-defined]
        ],
    }


def python_fixture_lines(vector_dir: Path) -> tuple[list[str], list[str], int]:
    """Run Python's own adapter over one vector, producing the same `EVENT`/`REPORT`/`ERROR` lines
    the `otel-genai-fixture` seam verb prints in TypeScript and Java (never a separate reference
    format), so the comparison below is apples to apples."""
    input_bytes = (vector_dir / "input.json").read_bytes()
    try:
        result = adapt(input_bytes, **_adapt_kwargs(vector_dir))
    except AdapterError as exc:
        return [], [f"ERROR {exc.reason}"], 1

    stdout: list[str] = []
    for event in result.events:
        try:
            line = canonical_string(event)
        except CanonicalizationError as exc:
            return [], [f"ERROR canonical:{exc.reason}"], 1
        stdout.append(f"EVENT {line}")
    stdout.append(
        f"REPORT {json.dumps(_report_json(result.report), separators=(',', ':'))}"
    )
    return stdout, [], 0


# --- TypeScript / Java: driven as built artifacts. ---------------------------------------------------


@functools.lru_cache(maxsize=1)
def _typescript_entry() -> Path:
    """The built TypeScript CLI entry point, resolved once -- every one of the 30 vectors drives
    the same build, so there is exactly one entry point to find, not one lookup per vector."""
    entry = TS_ENGINE / "dist" / "cli.js"
    if not entry.is_file():
        raise SystemExit(
            f"typescript dist is not built: {entry} is missing (run `pnpm build` in engines/typescript first)"
        )
    return entry


@functools.lru_cache(maxsize=1)
def _java_jar() -> Path:
    """The built Java runnable jar, resolved once -- same reasoning as `_typescript_entry`."""
    jars = sorted(
        (JAVA_ENGINE / "build" / "libs").glob("agentce-*-all.jar"),
        key=lambda p: p.stat().st_mtime,
    )
    if not jars:
        raise SystemExit(
            "java runnable jar is not built (run `./gradlew :assemble -q` in engines/java first)"
        )
    return jars[-1]


class EngineTimeout(Exception):
    """One TypeScript or Java run exceeded `ENGINE_TIMEOUT_S`."""


def _run_engine(cmd: list[str]) -> tuple[str, str, int]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, cwd=ROOT, timeout=ENGINE_TIMEOUT_S
        )
    except subprocess.TimeoutExpired as exc:
        raise EngineTimeout from exc
    return proc.stdout, proc.stderr, proc.returncode


def typescript_fixture(vector_dir: Path) -> tuple[str, str, int]:
    return _run_engine(
        ["node", str(_typescript_entry()), "otel-genai-fixture", str(vector_dir)]
    )


def java_fixture(vector_dir: Path) -> tuple[str, str, int]:
    return _run_engine(
        ["java", "-jar", str(_java_jar()), "otel-genai-fixture", str(vector_dir)]
    )


def copy_io_only(vector_dir: Path, dest: Path) -> None:
    """Copy only `input.json`/`adapt.json` -- never `expected*.json*` -- so a seam that merely
    echoes the directory it is given cannot pass."""
    shutil.copy2(vector_dir / "input.json", dest / "input.json")
    shutil.copy2(vector_dir / "adapt.json", dest / "adapt.json")


# --- Self-test: vectors against Python's own reference, and the seeded-fault discrimination case. ----


def check_vector_against_expected(vector_dir: Path, failures: list[str]) -> None:
    label = vector_dir.name
    stdout, stderr, code = python_fixture_lines(vector_dir)
    error_path = vector_dir / "expected-error.json"
    if error_path.exists():
        want_reason = json.loads(error_path.read_text(encoding="utf-8"))["reason"]
        if code != 1 or len(stderr) != 1:
            failures.append(
                f"{label}: expected a single ERROR line for reason {want_reason!r}, got {stderr!r}"
            )
            return
        got_reason = stderr[0].removeprefix("ERROR ")
        if got_reason != want_reason:
            failures.append(
                f"{label}: expected reason {want_reason!r}, got {got_reason!r}"
            )
        return
    if code != 0:
        failures.append(f"{label}: expected a clean run, got exit {code} ({stderr!r})")
        return
    got_events = [
        json.loads(line.removeprefix("EVENT "))
        for line in stdout
        if line.startswith("EVENT ")
    ]
    want_events = [
        json.loads(line)
        for line in (vector_dir / "expected.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    if got_events != want_events:
        failures.append(f"{label}: events do not match expected.jsonl")
    report_path = vector_dir / "expected-report.json"
    if report_path.exists():
        report_lines = [line for line in stdout if line.startswith("REPORT ")]
        if len(report_lines) != 1:
            failures.append(
                f"{label}: expected exactly one REPORT line, got {len(report_lines)}"
            )
        else:
            got_report = json.loads(report_lines[0].removeprefix("REPORT "))
            want_report = json.loads(report_path.read_text(encoding="utf-8"))
            if got_report != want_report:
                failures.append(f"{label}: report does not match expected-report.json")


def seed_truncation_enum_fault(stdout: list[str]) -> list[str]:
    """Apply, to a clean run's own `EVENT` lines, the exact two faults the `truncation-and-enums`
    vector exists to catch: round (not truncate) the one bare-number sub-millisecond timestamp, and
    pass through an out-of-enum tool-protocol/memory-trust value a correct filter would drop. Every
    other vector's whole-millisecond timestamps and in-enum values are untouched by this mutation,
    which is exactly why this vector -- and no other -- was built to carry it (contract round 2,
    point 1)."""
    mutated: list[str] = []
    for line in stdout:
        if not line.startswith("EVENT "):
            mutated.append(line)
            continue
        event = json.loads(line.removeprefix("EVENT "))
        data = event.get("data", {})
        event_type = data.get("@type")
        if (
            event_type == "ModelCall"
            and event.get("time") == "2023-11-14T22:13:20.123Z"
        ):
            event["time"] = "2023-11-14T22:13:20.124Z"  # rounds instead of truncates
        if event_type == "ToolCall" and isinstance(data.get("tool"), dict):
            data["tool"]["protocol"] = (
                "grpc"  # out-of-enum; a correct filter drops this
            )
        if event_type == "MemoryWrite":
            data["trust"] = "secret"  # out-of-enum
        if event_type == "MemoryRead":
            data["trust_min"] = "classified"  # out-of-enum
        mutated.append(f"EVENT {json.dumps(event, sort_keys=True)}")
    return mutated


def self_test() -> int:
    failures: list[str] = []
    vectors = all_vector_dirs()
    if len(vectors) != EXPECTED_VECTOR_COUNT:
        failures.append(
            f"expected exactly {EXPECTED_VECTOR_COUNT} vectors, found {len(vectors)}"
        )

    truncation_dir = None
    for vector_dir in vectors:
        if vector_dir.name == "truncation-and-enums":
            truncation_dir = vector_dir
        check_vector_against_expected(vector_dir, failures)

    if truncation_dir is None:
        failures.append("truncation-and-enums vector is missing")
    else:
        stdout, _stderr, code = python_fixture_lines(truncation_dir)
        if code != 0:
            failures.append(
                "truncation-and-enums: expected a clean run to seed the fault against"
            )
        else:
            mutated = seed_truncation_enum_fault(stdout)
            tamper_failures: list[str] = []
            compare(
                "truncation-and-enums",
                "\n".join(stdout),
                "\n".join(mutated),
                "reference",
                "seeded-fault stand-in",
                tamper_failures,
            )
            if not tamper_failures:
                failures.append(
                    "comparator failed to catch the seeded truncation/enum-filter fault on "
                    "truncation-and-enums -- this check's design cannot discriminate this fault class"
                )

    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if not failures:
        print(
            f"otel_genai_adapter_check self-test: all {EXPECTED_VECTOR_COUNT} vectors match Python's own "
            "reference; seeded-fault discrimination confirmed"
        )
    return 1 if failures else 0


# --- Real check: Python directly, against built TypeScript and Java artifacts. -----------------------


def run_real_check() -> int:
    failures: list[str] = []
    vectors = all_vector_dirs()
    if len(vectors) != EXPECTED_VECTOR_COUNT:
        failures.append(
            f"expected exactly {EXPECTED_VECTOR_COUNT} vectors, found {len(vectors)}"
        )

    for vector_dir in vectors:
        label = vector_dir.name
        with tempfile.TemporaryDirectory(prefix="otel-genai-parity-") as raw:
            tmp = Path(raw)
            copy_io_only(vector_dir, tmp)
            py_lines, py_err_lines, py_code = python_fixture_lines(tmp)
            py_out = "\n".join(py_lines)
            py_err = "\n".join(py_err_lines)
            results: dict[str, tuple[str, str, int]] = {}
            for engine, run in (
                ("typescript", typescript_fixture),
                ("java", java_fixture),
            ):
                try:
                    results[engine] = run(tmp)
                except EngineTimeout:
                    failures.append(
                        f"TIMEOUT: {label} ({engine}) exceeded {ENGINE_TIMEOUT_S} s"
                    )
            if len(results) != 2:
                continue
            ts_out, ts_err, ts_code = results["typescript"]
            java_out, java_err, java_code = results["java"]

            if not (py_code == ts_code == java_code):
                failures.append(
                    f"{label}: exit codes disagree (python={py_code}, typescript={ts_code}, java={java_code})"
                )
            py_combined = py_out if py_code == 0 else py_err
            ts_combined = ts_out.rstrip("\n") if ts_code == 0 else ts_err.strip()
            java_combined = (
                java_out.rstrip("\n") if java_code == 0 else java_err.strip()
            )
            compare(label, py_combined, ts_combined, "python", "typescript", failures)
            compare(label, py_combined, java_combined, "python", "java", failures)

    for failure in failures:
        prefix = "" if failure.startswith("TIMEOUT: ") else "MISMATCH: "
        print(f"{prefix}{failure}", file=sys.stderr)
    if failures:
        return 1
    print(
        f"MATCH: {len(vectors)} vectors byte-identical across python, typescript, java"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="otel_genai_adapter_check", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    return run_real_check()


if __name__ == "__main__":
    raise SystemExit(main())
