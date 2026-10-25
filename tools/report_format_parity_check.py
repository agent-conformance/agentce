"""report_format_parity_check - Python, TypeScript and Java write the same JUnit, CSV, OSCAL XML and public statement.

`report.junit.xml`, `report.csv`, `oscal-ar.xml` and `public-statement.md` (SPEC §9, §9.4, §9.5) are rendered by
Python's `render_junit`, `render_csv`, `render_oscal_xml` and `render_public_statement`; 18.16c ports them to the
TypeScript and Java engines. Until `assess --emit` is wired there (18.16b), each engine's `report-formats` seam
renders the four files from a report directory that engine's own `assess` wrote: its `assertions.json`, its
`oscal-ar.json` and the catalogs in its `manifest.json`.

Over `corpus/quickstart`, the auditor-view fixture (a deviations register, so the OSCAL carries `risks[]`) and the
30 generated corpus projects, Python's real `assess --emit junit,csv,oscal_xml,public` writes the goldens; each
other engine assesses the same input and its seam's four files must equal Python's byte for byte. Python's own
reconstruction from its report directory must also equal its assess output, so the seam's inputs are the ones
assess renders from. Then every case in `fixtures/report_formats/cases.json` (empty, every outcome, hostile
characters, an odd OSCAL document, code-point order) is rendered by all three engines from the same directory and
compared, and Python's rendering must equal the committed `expected.json` the engines' unit tests read.

Modes:
    report_format_parity_check.py                  # three engines (needs `pnpm build` and `:assemble` first)
    report_format_parity_check.py --fail-fast      # the same, stopping at the first mismatching run
    report_format_parity_check.py --engine python  # Python alone, no build needed
    report_format_parity_check.py --self-test      # the comparator catches every tamper class
    report_format_parity_check.py --write-expected # regenerate fixtures/report_formats/expected.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import claim_parity_check as cpc

ROOT = cpc.ROOT
CASES = Path(__file__).resolve().parent / "fixtures" / "report_formats" / "cases.json"
#: Python's four renderings of each case, the golden the TypeScript and Java unit tests read;
#: `--write-expected` regenerates it and every check run requires it current.
EXPECTED = CASES.with_name("expected.json")
FILES = ("report.junit.xml", "report.csv", "oscal-ar.xml", "public-statement.md")
ENGINES = cpc.ENGINES
#: quickstart, the auditor-view fixture and the 30 generated projects; a checker that silently drops a
#: run or a case must not print OK.
CORPUS_RUNS = 32

#: Python's side of the seam: the same four renderings from a report directory's own files, the way
#: write_report calls them (catalog labels `id@version`, the applied deviation ids).
PYTHON_RENDER = """
import json, sys
from pathlib import Path
from agentce.assertions import Assertion
from agentce.report import (applied_deviation_ids, render_csv, render_junit, render_oscal_xml,
                            render_public_statement)
out_root = Path(sys.argv[1])
for index, raw in enumerate(sys.argv[2:]):
    src = Path(raw)
    assertions = [Assertion.from_json(a) for a in json.loads((src / "assertions.json").read_text("utf-8"))]
    oscal = json.loads((src / "oscal-ar.json").read_text("utf-8"))
    manifest = json.loads((src / "manifest.json").read_text("utf-8"))
    catalogs = [f"{c['id']}@{c['version']}" for c in manifest["inputs"]["catalogs"]]
    out = out_root / str(index)
    out.mkdir(parents=True, exist_ok=True)
    rendered = {
        "report.junit.xml": render_junit(assertions),
        "report.csv": render_csv(assertions),
        "oscal-ar.xml": render_oscal_xml(oscal),
        "public-statement.md": render_public_statement(
            assertions, catalogs=catalogs, deviations=applied_deviation_ids(assertions)
        ),
    }
    for name, text in rendered.items():
        (out / name).write_bytes(text.encode("utf-8"))
"""


def _env() -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("AGENTCE_OPERATOR", "VIRTUAL_ENV")
    }
    env["LC_ALL"] = "C.UTF-8"
    return env


def render_with(engine: str, out_root: Path, sources: list[Path]) -> str:
    """Run one engine's `report-formats` seam over `sources`; output lands in `out_root/<index>/`.
    Returns the engine's stderr when it fails, else ""."""
    if engine == "python":
        cmd = [
            "uv",
            "run",
            "--frozen",
            "--project",
            str(cpc.spc.PY_ENGINE),
            "python",
            "-c",
            PYTHON_RENDER,
        ]
    elif engine == "typescript":
        cmd = ["node", str(cpc.spc.TS_ENGINE / "dist" / "seams.js"), "report-formats"]
    else:
        jar = cpc.spc.readiness_parity_check.java_jar()
        cmd = [
            "java",
            "-Dstdout.encoding=UTF-8",
            "-cp",
            str(jar),
            "org.agentce.Seams",
            "report-formats",
        ]
    proc = subprocess.run(
        [*cmd, str(out_root), *map(str, sources)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=_env(),
    )
    if proc.returncode != 0:
        return f"exit {proc.returncode}: {proc.stderr.strip()[-400:]}"
    return ""


def _first_difference(a: bytes, b: bytes) -> str:
    at = next(
        (i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y),
        min(len(a), len(b)),
    )
    return (
        f"first difference at byte {at} (python {a[at : at + 40]!r}, "
        f"engine {b[at : at + 40]!r}; lengths {len(a)} and {len(b)})"
    )


def file_problems(engine: str, reference: Path, candidate: Path) -> list[str]:
    """Every one of the four files in `candidate` must equal `reference`'s byte for byte."""
    problems = []
    for name in FILES:
        want = reference / name
        got = candidate / name
        if not want.is_file():
            problems.append(f"python wrote no {name}")
        elif not got.is_file():
            problems.append(f"{engine} wrote no {name}")
        elif want.read_bytes() != got.read_bytes():
            problems.append(
                f"{engine} {name}: "
                + _first_difference(want.read_bytes(), got.read_bytes())
            )
    return problems


def _assess(engine: str, args: list[str], env: dict[str, str], out: Path) -> int:
    """One engine's real `assess` (Python's with the four formats emitted); its exit code. Exits 1 and
    2 (a non-conformant or unproven outcome) still write the full report."""
    extra = ["--emit", "junit,csv,oscal_xml,public"] if engine == "python" else []
    _, code = cpc.run_engine(engine, ["assess", *args, *extra, "--out", str(out)], env)
    return code


def check_corpus(
    tmp: Path, engines: tuple[str, ...], fail_fast: bool
) -> tuple[int, int]:
    runs = [r for r in cpc.runs(cpc.generate_corpus(tmp / "corpus")) if r[3]]
    labels = [label for label, _, _, _ in runs]
    dirs = {
        e: [tmp / "assess" / e / label.replace("/", "_") for label in labels]
        for e in engines
    }
    jobs = [
        (e, args, env, dirs[e][i])
        for i, (_, args, env, _) in enumerate(runs)
        for e in engines
    ]
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
        codes = list(pool.map(lambda job: _assess(*job), jobs))
    exits = {(job[0], job[3]): code for job, code in zip(jobs, codes, strict=True)}
    seam_errors = {e: render_with(e, tmp / "seam" / e, dirs[e]) for e in engines}
    failures = 0
    for i, label in enumerate(labels):
        problems: list[str] = []
        py_exit = exits[("python", dirs["python"][i])]
        if py_exit not in (0, 1, 2):
            problems.append(f"python assess exited {py_exit}")
        for engine in engines:
            code = exits[(engine, dirs[engine][i])]
            if code != py_exit:
                problems.append(f"{engine} assess exited {code}, python {py_exit}")
            elif seam_errors[engine]:
                problems.append(f"{engine} report-formats seam: {seam_errors[engine]}")
            elif not problems:
                problems += file_problems(
                    engine, dirs["python"][i], tmp / "seam" / engine / str(i)
                )
        failures += _report(label, problems)
        if problems and fail_fast:
            break
    return failures, len(labels)


def write_case_dirs(root: Path) -> list[tuple[str, Path]]:
    """Each case as a report directory: assertions.json, oscal-ar.json (Python's render_oscal of the
    assertions unless the case gives its own document) and a manifest naming the case's catalogs.
    Keys are written in the order given, not sorted, so a renderer that stops sorting is seen."""
    cases = json.loads(CASES.read_text("utf-8"))["cases"]
    script = """
import json, sys
from agentce.assertions import Assertion
from agentce.report import render_oscal
print(json.dumps([render_oscal([Assertion.from_json(a) for a in c["assertions"]])
                  for c in json.load(open(sys.argv[1], encoding="utf-8"))["cases"]]))
"""
    proc = subprocess.run(
        [
            "uv",
            "run",
            "--frozen",
            "--project",
            str(cpc.spc.PY_ENGINE),
            "python",
            "-c",
            script,
            str(CASES),
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
        env=_env(),
    )
    oscals = json.loads(proc.stdout)
    out = []
    for case, oscal in zip(cases, oscals, strict=True):
        d = root / case["name"]
        d.mkdir(parents=True)
        catalogs = [
            dict(zip(("id", "version"), label.rsplit("@", 1), strict=True))
            for label in case["catalogs"]
        ]
        for name, value in (
            ("assertions.json", case["assertions"]),
            ("oscal-ar.json", case.get("oscal", oscal)),
            ("manifest.json", {"inputs": {"catalogs": catalogs}}),
        ):
            (d / name).write_text(json.dumps(value, indent=2) + "\n", "utf-8")
        out.append((case["name"], d))
    return out


def python_case_outputs(tmp: Path) -> dict[str, dict[str, str]]:
    """{case name: {file: text}} as Python renders the case table."""
    cases = write_case_dirs(tmp / "cases")
    error = render_with("python", tmp / "case-out" / "python", [d for _, d in cases])
    if error:
        raise SystemExit(f"python report-formats: {error}")
    return {
        name: {
            f: (tmp / "case-out" / "python" / str(i) / f).read_bytes().decode("utf-8")
            for f in FILES
        }
        for i, (name, _) in enumerate(cases)
    }


def _expected_text(outputs: dict[str, dict[str, str]]) -> str:
    return json.dumps(outputs, indent=2, ensure_ascii=True) + "\n"


def check_cases(
    tmp: Path, engines: tuple[str, ...], fail_fast: bool
) -> tuple[int, int]:
    outputs = python_case_outputs(tmp)
    failures = 0
    if not EXPECTED.is_file() or EXPECTED.read_text("utf-8") != _expected_text(outputs):
        print(
            f"MISMATCH: {EXPECTED.name} is not Python's current rendering of {CASES.name}; "
            "run report_format_parity_check.py --write-expected",
            file=sys.stderr,
        )
        failures += 1
    sources = [tmp / "cases" / name for name in outputs]
    errors = {e: render_with(e, tmp / "case-out" / e, sources) for e in engines[1:]}
    for i, name in enumerate(outputs):
        problems = []
        for engine in engines[1:]:
            if errors[engine]:
                problems.append(f"{engine} report-formats seam: {errors[engine]}")
            else:
                problems += file_problems(
                    engine,
                    tmp / "case-out" / "python" / str(i),
                    tmp / "case-out" / engine / str(i),
                )
        failures += _report(f"case {name}", problems)
        if problems and fail_fast:
            break
    return failures, len(outputs)


def write_expected() -> int:
    with tempfile.TemporaryDirectory(prefix="report-format-expected-") as raw:
        EXPECTED.write_text(_expected_text(python_case_outputs(Path(raw))), "utf-8")
    print(f"wrote {EXPECTED}")
    return 0


def _report(label: str, problems: list[str]) -> int:
    for problem in problems:
        print(f"MISMATCH: {label}: {problem}", file=sys.stderr)
    if not problems:
        print(f"MATCH: {label}")
    return 1 if problems else 0


def run_check(engines: tuple[str, ...], fail_fast: bool) -> int:
    with tempfile.TemporaryDirectory(prefix="report-format-parity-") as raw:
        tmp = Path(raw)
        # The case table runs first: it takes seconds, so --fail-fast stops on a renderer fault before
        # the corpus assess. Python's own seam over its assess output must give assess's own bytes, so
        # the seam's inputs (assertions.json, oscal-ar.json, the manifest's catalogs) are what assess
        # renders from.
        failures, runs = check_cases(tmp, engines, fail_fast)
        if not (failures and fail_fast):
            corpus_failures, corpus_runs = check_corpus(
                tmp / "corpus-run", engines, fail_fast
            )
            failures += corpus_failures
            runs += corpus_runs
    if failures:
        print(f"{failures} of {runs} runs differ", file=sys.stderr)
        return 1
    expected = CORPUS_RUNS + len(json.loads(CASES.read_text("utf-8"))["cases"])
    if runs != expected:
        print(f"ran {runs} runs, expected {expected}", file=sys.stderr)
        return 1
    print(f"REPORT FORMAT PARITY OK ({runs} runs)")
    return 0


def self_test() -> int:
    """A correct copy passes; each tamper class on one of the four files is caught."""
    good = {
        "report.junit.xml": b'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="agentce" />\n',
        "report.csv": b"control,subject,outcome,control_version,rung,mode\nA-01,s,conformant,1,2,automated\n",
        "oscal-ar.xml": b'<?xml version="1.0" encoding="UTF-8"?>\n<assessment-results><a>x</a></assessment-results>\n',
        "public-statement.md": b"# Public conformance statement\n\n## Basis\nx\n",
    }
    tampers: dict[str, tuple[str, bytes | None]] = {
        "one byte changed": (
            "report.csv",
            good["report.csv"].replace(b"A-01", b"A-02"),
        ),
        "file missing": ("oscal-ar.xml", None),
        "extra trailing newline": (
            "report.junit.xml",
            good["report.junit.xml"] + b"\n",
        ),
        "CRLF line endings": (
            "public-statement.md",
            good["public-statement.md"].replace(b"\n", b"\r\n"),
        ),
    }
    failures = []
    with tempfile.TemporaryDirectory(prefix="report-format-self-test-") as raw:
        tmp = Path(raw)

        def write(d: Path, files: dict[str, bytes | None]) -> Path:
            d.mkdir()
            for name, data in files.items():
                if data is not None:
                    (d / name).write_bytes(data)
            return d

        reference = write(tmp / "reference", dict(good))
        if file_problems("typescript", reference, write(tmp / "same", dict(good))):
            failures.append("a correct copy was flagged")
        for i, (name, (target, data)) in enumerate(tampers.items()):
            candidate = write(tmp / f"t{i}", {**good, target: data})
            if file_problems("java", reference, candidate):
                print(f"caught: {name}")
            else:
                failures.append(f"tamper not caught: {name}")
    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1
    print("SELF-TEST OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="report_format_parity_check", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--engine", choices=["python"], default=None)
    parser.add_argument("--write-expected", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.write_expected:
        return write_expected()
    return run_check(("python",) if args.engine else ENGINES, args.fail_fast)


if __name__ == "__main__":
    raise SystemExit(main())
