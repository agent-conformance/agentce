"""report_validate_parity_check - prove Python, TypeScript, and Java agree on `report --validate`.

`report --validate`'s job (SPEC S9, item 18.27) is schema-validating every artifact a report
directory holds; whichever engine ran `--validate` must reach the same verdict over the same
directory, since the directory itself carries no engine identity -- a report one engine wrote can be
(and in this check, always is) validated by a different engine entirely. Three independent per-engine
unit-test suites (the 21 cases each already has, mirroring
`harness/remediation/evidence/P18-18.27/python-reference.md`) can pass with three
independently-wrong-but-matching-in-no-way validators, since none of them ever compares against
another engine's real verdict over a shared directory. This check does.

* `--self-test` needs no build: a synthetic pair of "problem list" values, identical then a
  one-label tamper, must be told apart -- a check that always returns the same verdict regardless
  of its input must be caught here, the same discipline `diff_parity_check.py`'s self-test uses.
* The real invocation runs Python's own `agentce assess` (the console-script form, `uv run
  --frozen`) once, with every `--emit` token, to produce one shared, genuinely full report
  directory over the bundled quickstart project -- the same recipe
  `harness/remediation/evidence/P18-18.27/python-reference.md` captured its 21 cases against. Each
  of the 21 cases below copies that directory, applies its one named corruption (or none, for the
  clean case), then runs `report --validate <copy> --json` with Python's own console script, the
  built TypeScript `dist/cli.js`, and the built Java runnable jar, and asserts: the three exit codes
  agree, the three `valid` flags agree, and the three `problems` lists name the same files (and, for
  the two real-schema-only cases, the same third-party standard in parens) in the same order --
  message text is deliberately not compared byte-for-byte (each engine's own JSON Schema validator
  library phrases it differently; see the python reference's "Design notes").

Usage:
    report_validate_parity_check.py             # needs `pnpm build` (TS) and `:assemble` (Java) run first
    report_validate_parity_check.py --self-test
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
PY_ENGINE = ROOT / "engines" / "python"
TS_ENGINE = ROOT / "engines" / "typescript"
JAVA_ENGINE = ROOT / "engines" / "java"
QUICKSTART_DIR = ROOT / "corpus" / "quickstart"
CATALOG_DIR = ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"

ZERO_DIGEST = "sha256:" + "0" * 64


def _run(cmd: list[str], *, cwd: Path | None = None) -> tuple[str, int]:
    """Run `cmd`, returning `(stdout, exit_code)`; a non-zero exit is expected for most cases here,
    so the caller judges the exit code."""
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return proc.stdout, proc.returncode


def python_validate(report_dir: Path) -> tuple[str, int]:
    """The real Python engine's own `report --validate` (console-script form, so this script's own
    environment never needs `agentce` installed)."""
    return _run(
        [
            "uv",
            "run",
            "--frozen",
            "--project",
            str(PY_ENGINE),
            "agentce",
            "report",
            "--validate",
            str(report_dir),
            "--json",
        ],
        cwd=ROOT,
    )


def typescript_validate(report_dir: Path) -> tuple[str, int]:
    """The built TypeScript engine's `report --validate` (`pnpm build` must have run first)."""
    entry = TS_ENGINE / "dist" / "cli.js"
    if not entry.is_file():
        raise SystemExit(
            f"typescript dist is not built: {entry} is missing "
            "(run `pnpm build` in engines/typescript first)"
        )
    return _run(
        ["node", str(entry), "report", "--validate", str(report_dir), "--json"],
        cwd=ROOT,
    )


def java_validate(report_dir: Path) -> tuple[str, int]:
    """The built Java engine's `report --validate` (`./gradlew :assemble` must have run first)."""
    jars = sorted(
        (JAVA_ENGINE / "build" / "libs").glob("agentce-*-all.jar"),
        key=lambda p: p.stat().st_mtime,
    )
    if not jars:
        raise SystemExit(
            "java runnable jar is not built (run `./gradlew :assemble -q` in engines/java first)"
        )
    return _run(
        [
            "java",
            "-jar",
            str(jars[-1]),
            "report",
            "--validate",
            str(report_dir),
            "--json",
        ],
        cwd=ROOT,
    )


def build_base_report(directory: Path) -> Path:
    """Run Python's `assess` with every `--emit` token against the bundled quickstart project,
    producing one shared, genuinely full report directory -- the same recipe
    `harness/remediation/evidence/P18-18.27/python-reference.md` captured its 21 cases against."""
    full = directory / "full"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "--frozen",
            "--project",
            str(PY_ENGINE),
            "agentce",
            "assess",
            "--bundle",
            str(QUICKSTART_DIR / "evidence"),
            "--profile",
            str(QUICKSTART_DIR / "applicability.yaml"),
            "--domain",
            str(QUICKSTART_DIR / "domain.linkml.yaml"),
            "--catalog",
            "eu-ai-act@2026.09",
            "--catalog-dir",
            str(CATALOG_DIR),
            "--emit",
            "md,html,oscal,sarif,pack,junit,csv,oscal_xml",
            "--out",
            str(full),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode not in (0, 1):  # 1 = FINDINGS, still a real, complete report
        raise SystemExit(
            f"building the shared base report failed (exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )
    return full


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _register_output(report_dir: Path, filename: str) -> None:
    """Record `filename` in `manifest.json`'s own `outputs` map, exactly as `write_report`'s
    `extra_outputs` parameter would for an artifact outside the fixed `--emit` set (mirrors
    `harness/remediation/evidence/P18-18.27/python-reference.md` case 12/13's construction)."""
    manifest_path = report_dir / "manifest.json"
    manifest = _read_json(manifest_path)
    manifest["outputs"][filename] = ZERO_DIGEST
    _write_json(manifest_path, manifest)


# --- The 21 cases, each a (name, mutate, expect_exit) triple -------------------------------------


def _mutate_clean(report_dir: Path) -> None:
    pass


def _mutate_missing_mandatory(report_dir: Path) -> None:
    (report_dir / "assertions.json").unlink()


def _mutate_invalid_json(report_dir: Path) -> None:
    (report_dir / "manifest.json").write_text("not json{", encoding="utf-8")


def _mutate_local_schema(report_dir: Path) -> None:
    path = report_dir / "assertions.json"
    assertions = _read_json(path)
    del assertions[0]["control"]
    _write_json(path, assertions)


def _mutate_oscal_local(report_dir: Path) -> None:
    path = report_dir / "oscal-ar.json"
    oscal = _read_json(path)
    del oscal["assessment-results"]
    _write_json(path, oscal)


def seed_oscal_nist_fault(report_dir: Path) -> None:
    """The real-schema-only OSCAL violation `case6-oscal-nist` uses -- public (unlike this module's
    other per-case mutators) so `verification/gates/report_validate_engines.sh` (VG-REPORT-VALIDATE)
    can call it directly; the one case C3's own `REAL_SCHEMA_MARKERS` names as the discriminating
    proof an engine skipping the real third-party standard would wrongly pass."""
    path = report_dir / "oscal-ar.json"
    oscal = _read_json(path)
    oscal["assessment-results"]["uuid"] = "not-a-uuid"
    _write_json(path, oscal)


def _mutate_sarif_local(report_dir: Path) -> None:
    path = report_dir / "results.sarif"
    sarif = _read_json(path)
    del sarif["runs"]
    _write_json(path, sarif)


def _mutate_sarif_real(report_dir: Path) -> None:
    path = report_dir / "results.sarif"
    sarif = _read_json(path)
    results = sarif["runs"][0]["results"]
    if not results:
        results.append(
            {
                "ruleId": "x",
                "level": "note",
                "message": {"text": "x"},
                "locations": [],
                "partialFingerprints": {},
            }
        )
    results[0]["message"]["bogus_field"] = "x"
    _write_json(path, sarif)


def _mutate_csv(report_dir: Path) -> None:
    (report_dir / "report.csv").write_text(
        "not a csv file at all, just garbage\x00\x01\n", encoding="utf-8"
    )


def _mutate_xml(report_dir: Path) -> None:
    (report_dir / "report.junit.xml").write_text("<not-closed>", encoding="utf-8")
    (report_dir / "oscal-ar.xml").write_text("<not-closed>", encoding="utf-8")


def _mutate_jsonl_clean(report_dir: Path) -> None:
    _register_output(report_dir, "runtime_drift.jsonl")
    (report_dir / "runtime_drift.jsonl").write_text(
        '{"subject": "x"}\n{"subject": "y"}\n', encoding="utf-8"
    )


def _mutate_jsonl_corrupt(report_dir: Path) -> None:
    _register_output(report_dir, "runtime_drift.jsonl")
    (report_dir / "runtime_drift.jsonl").write_text(
        '{"subject": "x"}\nnot json at all\n', encoding="utf-8"
    )


def _mutate_empty_md(report_dir: Path) -> None:
    (report_dir / "report.md").write_text("   \n", encoding="utf-8")


def _mutate_recorded_missing(report_dir: Path) -> None:
    (report_dir / "report.html").unlink()


def _mutate_xml_multi_root(report_dir: Path) -> None:
    (report_dir / "report.junit.xml").write_text(
        '<?xml version="1.0"?>\n<a/><b/>\n', encoding="utf-8"
    )


def _mutate_xml_undefined_entity(report_dir: Path) -> None:
    (report_dir / "oscal-ar.xml").write_text(
        '<?xml version="1.0"?>\n<a>&undefined;</a>\n', encoding="utf-8"
    )


def _mutate_non_object_manifest(report_dir: Path) -> None:
    (report_dir / "manifest.json").write_text("[1, 2, 3]", encoding="utf-8")


def _mutate_non_utf8_mandatory(report_dir: Path) -> None:
    (report_dir / "assertions.json").write_bytes(b"\xff\xfe\x00\x01not-utf8")


def _mutate_non_utf8_jsonl(report_dir: Path) -> None:
    _register_output(report_dir, "runtime_drift.jsonl")
    (report_dir / "runtime_drift.jsonl").write_bytes(
        b'{"subject": "x"}\n\xff\xfenot-utf8'
    )


def _mutate_local_multi(report_dir: Path) -> None:
    """Two local-stage violations in one file (P18-18.27 verifier round 1, `two_locals`): before the
    fix, Python's `jsonschema.validate` raised only its single `best_match` error while TS/Java's
    `allErrors: true` validators already reported both -- a real count divergence `case4-local-schema`
    (one violation) could never catch."""
    path = report_dir / "assertions.json"
    assertions = _read_json(path)
    del assertions[0]["control"]
    del assertions[1]["control"]
    _write_json(path, assertions)


def _mutate_sarif_anyof(report_dir: Path) -> None:
    """A real-schema `anyOf` failure (P18-18.27 verifier round 1, `anyof_region`): an empty `region`
    object fails all three of SARIF's `anyOf` branches (`startLine`, `charOffset`, `byteOffset`).
    Python's `iter_errors` reports exactly the one combinator error; Ajv's `allErrors: true` also
    reported every failing branch, and networknt reported every failing branch but never the
    combinator error itself -- three different counts (1, 4, 3) before the fix collapsed all three to
    one message per combinator failure."""
    path = report_dir / "results.sarif"
    sarif = _read_json(path)
    sarif["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"] = {}
    _write_json(path, sarif)


def _mutate_sarif_idx_order(report_dir: Path) -> None:
    """Violations at array indices 2 and 10 in the same file (P18-18.27 verifier round 1,
    `idx_order`): before the fix, TS sorted the joined location string lexicographically (`results/10`
    before `results/2`) and Java's `Json.byteCompare` did the same, while Python's `absolute_path`
    elements are already native ints and sorted correctly -- needs at least 11 `results` entries in the
    shared base report."""
    path = report_dir / "results.sarif"
    sarif = _read_json(path)
    results = sarif["runs"][0]["results"]
    if len(results) <= 10:
        raise AssertionError(
            f"fixture needs at least 11 SARIF results for the idx_order case, has {len(results)}"
        )
    del results[2]["message"]["text"]
    del results[10]["message"]["text"]
    _write_json(path, sarif)


CASES: list[tuple[str, Callable[[Path], None], int]] = [
    ("case1-clean", _mutate_clean, 0),
    ("case2-missing-mandatory", _mutate_missing_mandatory, 3),
    ("case3-invalid-json", _mutate_invalid_json, 3),
    ("case4-local-schema", _mutate_local_schema, 3),
    ("case5-oscal-local", _mutate_oscal_local, 3),
    ("case6-oscal-nist", seed_oscal_nist_fault, 3),
    ("case7-sarif-local", _mutate_sarif_local, 3),
    ("case8-sarif-real", _mutate_sarif_real, 3),
    ("case9-csv", _mutate_csv, 3),
    ("case10-11-xml", _mutate_xml, 3),
    ("case12-jsonl-clean", _mutate_jsonl_clean, 0),
    ("case13-jsonl-corrupt", _mutate_jsonl_corrupt, 3),
    ("case14-empty-md", _mutate_empty_md, 3),
    ("case15-recorded-missing", _mutate_recorded_missing, 3),
    ("case17-xml-multi-root", _mutate_xml_multi_root, 3),
    ("case18-xml-undefined-entity", _mutate_xml_undefined_entity, 3),
    ("case19-non-object-manifest", _mutate_non_object_manifest, 3),
    ("case20-non-utf8-mandatory", _mutate_non_utf8_mandatory, 3),
    ("case21-non-utf8-jsonl", _mutate_non_utf8_jsonl, 3),
    ("case22-local-multi", _mutate_local_multi, 3),
    ("case23-sarif-anyof", _mutate_sarif_anyof, 3),
    ("case24-sarif-idx-order", _mutate_sarif_idx_order, 3),
]

#: Cases that must additionally prove the real third-party schema ran, not only the local profile
#: (python reference, cases 6 and 8): at least one problem label names the standard in parens.
REAL_SCHEMA_MARKERS = {
    "case6-oscal-nist": "oscal-ar.json (NIST OSCAL 1.1.2)",
    "case8-sarif-real": "results.sarif (OASIS SARIF 2.1.0)",
    "case23-sarif-anyof": "results.sarif (OASIS SARIF 2.1.0)",
}


def problem_labels(problems: list[str]) -> list[str]:
    """Each problem's file (and, for a real-schema-only failure, standard-in-parens) label -- the
    part before the first `": "` -- with the trailing validator message dropped. Message text is
    deliberately not compared (see the module docstring); the label is."""
    return [p.split(": ", 1)[0] for p in problems]


def problem_prefixes(problems: list[str]) -> list[str]:
    """Each problem's label plus, for a schema-validation problem, its location (`problem_labels`
    only compares the file label, so three engines reporting a different count, order, or location of
    problems *within* the same file were invisible to it -- the real gap the P18-18.27 verifier round
    1 found). A schema-validation problem's shape is `"<label>: <location>: <message>"`, where
    `<location>` (a `/`-joined path, or `<root>`) never contains a space; every other problem's
    message does, so testing for a space after the first `": "` tells the two shapes apart without
    tracking which mutator produced which."""
    out = []
    for p in problems:
        first = p.find(": ")
        if first == -1:
            out.append(p)
            continue
        rest = p[first + 2 :]
        second = rest.find(": ")
        candidate_location = rest if second == -1 else rest[:second]
        if candidate_location == "" or " " in candidate_location:
            out.append(p[:first])
        else:
            out.append(p[: first + 2 + len(candidate_location)])
    return out


def compare_labels(
    case: str, py: list[str], ts: list[str], java: list[str], failures: list[str]
) -> None:
    if not (py == ts == java):
        failures.append(
            f"{case}: problem labels disagree\n  python={py}\n  typescript={ts}\n  java={java}"
        )


def self_test() -> int:
    failures: list[str] = []

    unmoved: list[str] = []
    compare_labels("comparator-self-test", ["a.json"], ["a.json"], ["a.json"], unmoved)
    if unmoved:
        failures.append(
            "comparator wrongly flagged two identical label lists as a mismatch"
        )
    caught: list[str] = []
    compare_labels("comparator-self-test", ["a.json"], ["a.json"], ["b.json"], caught)
    if not caught:
        failures.append(
            "comparator failed to catch a one-label tamper between three label lists"
        )

    # A real divergence this script actually caught during this item's own development (contract
    # critic round 1, F2 and F6): before the fix, TypeScript's `XMLValidator.validate` accepted a
    # self-closing root followed by a sibling element outright, so TS reported no problem for
    # `report.junit.xml` where Python's `ElementTree` and Java's `XMLStreamReader` both did -- these
    # are the three engines' real `problem_labels()` output for that exact case, not synthetic
    # `a.json`/`b.json` strings, so this self-test exercises the comparator against the shape of
    # divergence the real check exists to catch, not just an arbitrary tamper.
    real_divergence: list[str] = []
    compare_labels(
        "self-test-real-divergence-shape",
        problem_labels(
            [
                "report.junit.xml: invalid XML (junk after document element: line 2, column 4)"
            ]
        ),
        problem_labels([]),  # the pre-fix TypeScript bug: no problem reported at all
        problem_labels(
            [
                "report.junit.xml: invalid XML (The markup in the document following the root element must be well-formed.)"
            ]
        ),
        real_divergence,
    )
    if not real_divergence:
        failures.append(
            "comparator failed to catch the real pre-fix TS/Java-vs-Python divergence shape (F2/F6)"
        )

    label_cases = [
        (
            "plain",
            "assertions.json: 'control' is a required property",
            "assertions.json",
        ),
        (
            "invalid-json",
            "manifest.json: invalid JSON (Expecting value)",
            "manifest.json",
        ),
        (
            "real-schema",
            "oscal-ar.json (NIST OSCAL 1.1.2): assessment-results/uuid: 'not-a-uuid' does not match '...'",
            "oscal-ar.json (NIST OSCAL 1.1.2)",
        ),
    ]
    for name, problem, expected in label_cases:
        got = problem_labels([problem])[0]
        if got != expected:
            failures.append(
                f"label extraction ({name}): got {got!r}, expected {expected!r}"
            )

    prefix_cases = [
        (
            "schema-with-location",
            "assertions.json: 0: 'control' is a required property",
            "assertions.json: 0",
        ),
        (
            "real-schema-with-location",
            "oscal-ar.json (NIST OSCAL 1.1.2): assessment-results/uuid: 'not-a-uuid' does not match '...'",
            "oscal-ar.json (NIST OSCAL 1.1.2): assessment-results/uuid",
        ),
        (
            "no-location-colon-in-message",
            "assertions.json: cannot read ('utf-8' codec can't decode byte 0xff in position 0: invalid start byte)",
            "assertions.json",
        ),
        (
            "plain-no-location",
            "manifest.json: invalid JSON (Expecting value)",
            "manifest.json",
        ),
    ]
    for name, problem, expected in prefix_cases:
        got = problem_prefixes([problem])[0]
        if got != expected:
            failures.append(
                f"prefix extraction ({name}): got {got!r}, expected {expected!r}"
            )

    # The real pre-fix divergence `problem_labels` (file-label only) could never see (P18-18.27
    # verifier round 1, `anyof_region`): all three engines name the same file and standard, so
    # `problem_labels` alone called this a match, but they disagreed on count (1 vs 4 vs 3) at the
    # same location -- `problem_prefixes` must still tell a single combined-location problem apart
    # from the same location repeated.
    location_count_divergence: list[str] = []
    region = "runs/0/results/0/locations/0/physicalLocation/region"
    compare_labels(
        "self-test-location-count-divergence",
        problem_prefixes([f"results.sarif (OASIS SARIF 2.1.0): {region}: combinator message"]),
        problem_prefixes(
            [
                f"results.sarif (OASIS SARIF 2.1.0): {region}: branch 1",
                f"results.sarif (OASIS SARIF 2.1.0): {region}: branch 2",
            ]
        ),
        problem_prefixes([f"results.sarif (OASIS SARIF 2.1.0): {region}: combinator message"]),
        location_count_divergence,
    )
    if not location_count_divergence:
        failures.append(
            "comparator failed to catch a same-location problem-count divergence between engines"
        )

    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if not failures:
        print(
            "report_validate_parity_check self-test: comparator + label extraction discriminate"
        )
    return 1 if failures else 0


def run_real_check() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="report-validate-parity-") as raw:
        tmp = Path(raw)
        base = build_base_report(tmp)

        for case, mutate, expect_exit in CASES:
            case_dir = tmp / case
            shutil.copytree(base, case_dir)
            mutate(case_dir)

            py_out, py_code = python_validate(case_dir)
            ts_out, ts_code = typescript_validate(case_dir)
            java_out, java_code = java_validate(case_dir)

            if not (
                py_code == expect_exit
                and ts_code == expect_exit
                and java_code == expect_exit
            ):
                failures.append(
                    f"{case}: expected exit {expect_exit} in all three engines, got "
                    f"python={py_code}, typescript={ts_code}, java={java_code}"
                )
                continue

            py_env, ts_env, java_env = (
                json.loads(py_out),
                json.loads(ts_out),
                json.loads(java_out),
            )
            if not (py_env["valid"] == ts_env["valid"] == java_env["valid"]):
                failures.append(
                    f"{case}: 'valid' disagrees "
                    f"(python={py_env['valid']}, typescript={ts_env['valid']}, java={java_env['valid']})"
                )

            py_labels = problem_labels(py_env["problems"])
            ts_labels = problem_labels(ts_env["problems"])
            java_labels = problem_labels(java_env["problems"])
            py_prefixes = problem_prefixes(py_env["problems"])
            ts_prefixes = problem_prefixes(ts_env["problems"])
            java_prefixes = problem_prefixes(java_env["problems"])
            compare_labels(case, py_prefixes, ts_prefixes, java_prefixes, failures)

            marker = REAL_SCHEMA_MARKERS.get(case)
            if marker is not None:
                for engine_name, labels in (
                    ("python", py_labels),
                    ("typescript", ts_labels),
                    ("java", java_labels),
                ):
                    if marker not in labels:
                        failures.append(
                            f"{case}: {engine_name} never named the real third-party standard "
                            f"({marker!r}); labels were {labels}"
                        )

        # Case 16: a path that is not a directory refuses before any artifact is opened. Each
        # engine's own error-envelope field name differs (`error.key` in Python, `error.message_key`
        # in TS/Java, the same split `diff_parity_check.py` already documents), so this reads each by
        # its own name rather than assuming a single shared field.
        missing = tmp / "does-not-exist"
        py_out, py_code = python_validate(missing)
        ts_out, ts_code = typescript_validate(missing)
        java_out, java_code = java_validate(missing)
        if not (py_code == 3 and ts_code == 3 and java_code == 3):
            failures.append(
                f"case16-not-a-directory: expected exit 3 in all three engines, got "
                f"python={py_code}, typescript={ts_code}, java={java_code}"
            )
        py_key = json.loads(py_out)["error"]["key"]
        ts_key = json.loads(ts_out)["error"]["message_key"]
        java_key = json.loads(java_out)["error"]["message_key"]
        expected_key = "input.validate_not_a_directory"
        if not (py_key == ts_key == java_key == expected_key):
            failures.append(
                f"case16-not-a-directory: error key mismatch, expected {expected_key!r} in all three, got "
                f"python={py_key!r}, typescript={ts_key!r}, java={java_key!r}"
            )

    for failure in failures:
        print(f"MISMATCH: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(f"MATCH: {len(CASES) + 1} invocations agree across python, typescript, java")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="report_validate_parity_check", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    return run_real_check()


if __name__ == "__main__":
    raise SystemExit(main())
