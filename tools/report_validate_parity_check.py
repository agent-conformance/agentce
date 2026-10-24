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

* The reader-case pass (SPEC 8.7 report-file reader contract, item 18.60) reads
  `spec/report/reader-cases.json`. Each engine writes its own full base report with its own `assess`
  (an engine that refuses `--emit` with `input.emit_unsupported` writes its core outputs and gets
  report.csv, report.junit.xml and oscal-ar.xml from Python's base). For every case and every engine it
  copies that engine's base, applies the case's edits and runs the case's command in that engine:
  `report --validate <dir> --json`, `readiness <dir> --json`, `sign --json <dir> --as claimant --profile
  kms --key tools/fixtures/sign/test-key.pem`, or `diff <base>/<file> <dir>/<file> --json`. It asserts
  the exit equals the case's; on exit 3 the key (validate's top-level `message_key`, an input error's
  Python `error.key` or TypeScript and Java `error.message_key`) equals the case's, `names` is in a
  problem line that names the file (validate) or in the error's text, the run changed nothing in the
  folder (for sign: no signature written, claim.json unchanged; for readiness: no readiness file), and
  the three engines name the same reader-contract rules at the same lines. Each engine's clean base must
  also validate 0 in all three engines, read READY and sign with exit 0. The self-test judges the real
  table with synthetic answers: a matching answer passes, the case with its exit flipped fails, an
  fake engine that always answers valid fails every refusal, and so do a refusal for the wrong reason, a
  wrong key, a refused sign that wrote a signature and engines naming different lines.

Usage:
    report_validate_parity_check.py                  # needs `pnpm build` (TS) and `:assemble` (Java) run first
    report_validate_parity_check.py --reader-cases   # the reader-case pass only
    report_validate_parity_check.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
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
    return _run(
        ["node", str(_ts_entry()), "report", "--validate", str(report_dir), "--json"],
        cwd=ROOT,
    )


def java_validate(report_dir: Path) -> tuple[str, int]:
    """The built Java engine's `report --validate` (`./gradlew :assemble` must have run first)."""
    return _run(
        [
            "java",
            "-jar",
            str(_java_jar()),
            "report",
            "--validate",
            str(report_dir),
            "--json",
        ],
        cwd=ROOT,
    )


QUICKSTART_ARGS = [
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
]
PROJECT_VIEW_FIXTURE = ROOT / "verification" / "gates" / "fixtures" / "project_view"


def _assess(out: Path, args: list[str]) -> Path:
    """Python's own `agentce assess ARGS --out OUT` (exit 1 = FINDINGS, exit 2 =
    INSUFFICIENT_EVIDENCE on a severity:high control, SPEC.md:1076 -- both still a complete
    report; `build_view_bases`'s project-view fixture has exactly this shape)."""
    proc = subprocess.run(
        ["uv", "run", "--frozen", "--project", str(PY_ENGINE), "agentce", "assess"]
        + args
        + ["--out", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode not in (0, 1, 2):
        raise SystemExit(
            f"building the base report {out.name} failed (exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )
    return out


ENGINES = (
    ("python", python_validate),
    ("typescript", typescript_validate),
    ("java", java_validate),
)


def build_base_report(directory: Path) -> Path:
    """Run Python's `assess` with every `--emit` token against the bundled quickstart project,
    producing one shared, genuinely full report directory -- the same recipe
    `harness/remediation/evidence/P18-18.27/python-reference.md` captured its 21 cases against."""
    return _assess(
        directory / "full",
        QUICKSTART_ARGS + ["--emit", "md,html,oscal,sarif,pack,junit,csv,oscal_xml"],
    )


def build_view_bases(directory: Path) -> list[Path]:
    """One report per reader view, since `buyer.json`, `security.json`, `auditor.json` and
    `project.json` are each written only by their own `--for` run. `project.json` needs two or more
    subjects, so it comes from the project-view gate's fixture (an unsigned test catalog, which only
    Python checks signatures on)."""
    bases = [
        _assess(directory / view, QUICKSTART_ARGS + ["--for", view])
        for view in ("buyer", "security", "auditor")
    ]
    fixture = PROJECT_VIEW_FIXTURE
    bases.append(
        _assess(
            directory / "project",
            [
                "--bundle",
                str(fixture / "evidence"),
                "--profile",
                str(fixture / "applicability.yaml"),
                "--domain",
                str(fixture / "domain.linkml.yaml"),
                "--catalog-dir",
                str(fixture / "catalog"),
                "--for",
                "risk-lead",
                "--allow-unverified-catalog",
            ],
        )
    )
    return bases


def discover_census_mutations(bases: list[Path]) -> dict[str, Any]:
    """`report_validate_census_cases.py`'s plan over `bases`: one mutation per census (schema,
    keyword) pair, found with the Python engine's own validators, plus any pair it could not reach."""
    proc = subprocess.run(
        [
            "uv",
            "run",
            "--frozen",
            "--project",
            str(PY_ENGINE),
            "python",
            str(ROOT / "tools" / "report_validate_census_cases.py"),
        ]
        + [str(b) for b in bases],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"census mutation search failed:\n{proc.stderr}")
    plan: dict[str, Any] = json.loads(proc.stdout)
    return plan


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


def _mutate_recorded_missing_multi(report_dir: Path) -> None:
    """Three recorded outputs missing at once (18.61): `report.md`, `results.sarif` and
    `report.html` all deleted after a run recorded them. With a single missing output
    (`_mutate_recorded_missing`), the three engines' problem order can never diverge; with two or
    more, Python previously iterated a set union whose order followed `PYTHONHASHSEED`."""
    for filename in ("report.md", "results.sarif", "report.html"):
        (report_dir / filename).unlink()


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


def _mutate_ref_branch(report_dir: Path) -> None:
    """A `pattern` reached through `$ref` (P18-18.27 verifier round 2, D1)."""
    path = report_dir / "oscal-ar.json"
    oscal = _read_json(path)
    finding = oscal["assessment-results"]["results"][0]["findings"][0]
    finding["target"]["status"]["reason"] = "has space"
    _write_json(path, oscal)


def _mutate_two_keywords_one_location(report_dir: Path) -> None:
    """`pattern` and `enum` failing at one location under `allOf` (round 2, ordering ties)."""
    path = report_dir / "oscal-ar.json"
    oscal = _read_json(path)
    finding = oscal["assessment-results"]["results"][0]["findings"][0]
    finding["target"]["status"]["state"] = "bad state"
    _write_json(path, oscal)


def _mutate_sarif_two_extra(report_dir: Path) -> None:
    """Two unexpected keys on one SARIF object: one `additionalProperties` problem (round 2, D3)."""
    path = report_dir / "results.sarif"
    sarif = _read_json(path)
    sarif["runs"][0]["results"][0]["message"].update(bogus2=1, bogus1=1)
    _write_json(path, sarif)


def _mutate_local_two_extra(report_dir: Path) -> None:
    """Two unexpected keys at the local-profile stage (round 2, D3)."""
    path = report_dir / "assertions.json"
    assertions = _read_json(path)
    assertions[0].update(zz=1, aa=2)
    _write_json(path, assertions)


def _mutate_sarif_format(report_dir: Path) -> None:
    """A malformed `uri`: `format` is an annotation in all three engines, so still valid (D4)."""
    path = report_dir / "results.sarif"
    sarif = _read_json(path)
    sarif["runs"][0]["tool"]["driver"]["informationUri"] = "not a uri :: at all"
    _write_json(path, sarif)


def _mutate_digit_and_slash_keys(report_dir: Path) -> None:
    """Object keys `9`/`10` (compare as strings) and `/`-containing keys (JSON-pointer escaped) in
    one map (round 2, D5 and D6)."""
    path = report_dir / "manifest.json"
    manifest = _read_json(path)
    for key in ("9", "10", "packs/x.json", "packs-old.json"):
        manifest["outputs"][key] = "bad"
    _write_json(path, manifest)


def _mutate_sarif_oneof_two_passing(report_dir: Path) -> None:
    """A SARIF `graphTraversal` matching both `oneOf` branches: one problem, the combinator's own
    (round 2, D2)."""
    path = report_dir / "results.sarif"
    sarif = _read_json(path)
    sarif["runs"][0]["results"][0]["graphTraversals"] = [
        {"runGraphIndex": 0, "resultGraphIndex": 0}
    ]
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
    ("case25-ref-branch", _mutate_ref_branch, 3),
    ("case26-two-keywords-one-location", _mutate_two_keywords_one_location, 3),
    ("case27-sarif-two-extra", _mutate_sarif_two_extra, 3),
    ("case28-local-two-extra", _mutate_local_two_extra, 3),
    ("case29-sarif-format", _mutate_sarif_format, 0),
    ("case30-digit-and-slash-keys", _mutate_digit_and_slash_keys, 3),
    ("case31-sarif-oneof-two-passing", _mutate_sarif_oneof_two_passing, 3),
    ("case32-recorded-missing-multi", _mutate_recorded_missing_multi, 3),
]


def _mutate_optional_mode000(report_dir: Path) -> None:
    (report_dir / "auditor.json").write_text(
        '{"not": "an auditor pack"}\n', encoding="utf-8"
    )
    (report_dir / "auditor.json").chmod(0)


def _mutate_optional_unenterable(report_dir: Path) -> None:
    """auditor.json behind a link into a folder that can be listed but not entered (18.167)."""
    lock = report_dir / "lock"
    lock.mkdir()
    (lock / "auditor.json").write_text('{"not": "an auditor pack"}\n', encoding="utf-8")
    (report_dir / "auditor.json").symlink_to(lock / "auditor.json")
    lock.chmod(0o600)


def _mutate_data_fifo(report_dir: Path) -> None:
    (report_dir / "coverage.json").unlink()
    os.mkfifo(report_dir / "coverage.json")


def _mutate_manifest_fifo(report_dir: Path) -> None:
    (report_dir / "manifest.json").unlink()
    os.mkfifo(report_dir / "manifest.json")


def _mutate_claim_absent(report_dir: Path) -> None:
    (report_dir / "claim.json").unlink()


def _mutate_data_recorded_absent(report_dir: Path) -> None:
    (report_dir / "integrity.jsonl").unlink()


def _mutate_data_dangling(report_dir: Path) -> None:
    (report_dir / "quarantine.jsonl").unlink()
    (report_dir / "quarantine.jsonl").symlink_to(report_dir / "nowhere")


def _restore_modes(report_dir: Path) -> None:
    """Give back what the unreadable cases took, so the temporary folder can be removed."""
    for path in (report_dir, *report_dir.rglob("*")):
        if not path.is_symlink():
            path.chmod(0o700)


#: The cases where a file is there but cannot be read, or a file every run writes is gone (18.167):
#: each must give the same exit 3, the key report.validation_failed and the same problem lines, word
#: for word, in all three engines. An unreadable entry is never read as absent.
READ_CASES: list[tuple[str, Callable[[Path], None], list[str]]] = [
    (
        "case33-optional-mode000",
        _mutate_optional_mode000,
        ["auditor.json: cannot read (permission denied)"],
    ),
    (
        "case34-optional-unenterable",
        _mutate_optional_unenterable,
        ["auditor.json: cannot read (permission denied)"],
    ),
    (
        "case35-data-fifo",
        _mutate_data_fifo,
        ["coverage.json: cannot read (not a regular file)"],
    ),
    (
        "case36-manifest-fifo",
        _mutate_manifest_fifo,
        ["manifest.json: cannot read (not a regular file)"],
    ),
    (
        "case37-claim-absent",
        _mutate_claim_absent,
        [
            "claim.json: missing (assertions.json holds assertions, and a run that records any"
            " writes claim.json)"
        ],
    ),
    (
        "case38-data-recorded-absent",
        _mutate_data_recorded_absent,
        [
            "integrity.jsonl: missing (recorded in manifest.json's outputs but not on disk)"
        ],
    ),
    (
        "case39-data-dangling-link",
        _mutate_data_dangling,
        ["quarantine.jsonl: cannot read (not a regular file)"],
    ),
]

REPORT_INVALID = "report.validation_failed"


def run_read_cases(tmp: Path, base: Path, failures: list[str]) -> int:
    for case, mutate, expected in READ_CASES:
        case_dir = tmp / case
        shutil.copytree(base, case_dir)
        mutate(case_dir)
        try:
            runs = {
                "python": python_validate(case_dir),
                "typescript": typescript_validate(case_dir),
                "java": java_validate(case_dir),
            }
        finally:
            _restore_modes(case_dir)
        for engine_name, (out, code) in runs.items():
            env = json.loads(out) if code == 3 else {}
            got = (code, env.get("message_key"), env.get("problems"))
            if got != (3, REPORT_INVALID, expected):
                failures.append(
                    f"{case}: {engine_name} answered exit {code}, key {got[1]!r}, problems "
                    f"{got[2]!r}; expected exit 3, {REPORT_INVALID!r}, {expected!r}"
                )
    return len(READ_CASES)


#: Cases that must additionally prove the real third-party schema ran, not only the local profile
#: (python reference, cases 6 and 8): at least one problem label names the standard in parens.
NIST_LABEL = "oscal-ar.json (NIST OSCAL 1.1.2)"
SARIF_LABEL = "results.sarif (OASIS SARIF 2.1.0)"
REAL_SCHEMA_MARKERS = {
    "case6-oscal-nist": NIST_LABEL,
    "case8-sarif-real": SARIF_LABEL,
    "case23-sarif-anyof": SARIF_LABEL,
}


def problem_labels(problems: list[str]) -> list[str]:
    """Each problem's file (and, for a real-schema-only failure, standard-in-parens) label -- the
    part before the first `": "` -- with the trailing validator message dropped. Message text is
    deliberately not compared (see the module docstring); the label is."""
    return [p.split(": ", 1)[0] for p in problems]


def _split_schema_problem(problem: str) -> tuple[str, str | None]:
    """`(prefix, message)` for a schema-validation problem, `(label, None)` for any other.

    A schema-validation problem's shape is `"<label>: <location>: <message>"`, where `<location>` (a
    `/`-joined path, or `<root>`) never contains a space; every other problem's message does, so
    testing for a space after the first `": "` tells the two shapes apart without tracking which
    mutator produced which."""
    first = problem.find(": ")
    if first == -1:
        return problem, None
    rest = problem[first + 2 :]
    second = rest.find(": ")
    candidate_location = rest if second == -1 else rest[:second]
    if candidate_location == "" or " " in candidate_location:
        return problem[:first], None
    location_end = first + 2 + len(candidate_location)
    if (
        second == -1
    ):  # `"<label>: missing"`, `"<label>: empty"`: no message, so not a schema problem
        return problem[:location_end], None
    return problem[:location_end], problem[location_end + 2 :]


def problem_prefixes(problems: list[str]) -> list[str]:
    """Each problem's label plus, for a schema-validation problem, its location (`problem_labels`
    only compares the file label, so three engines reporting a different count, order, or location of
    problems *within* the same file were invisible to it -- the real gap the P18-18.27 verifier round
    1 found)."""
    return [_split_schema_problem(p)[0] for p in problems]


#: Keywords reported under one message family: Python's `jsonschema` words `anyOf` and `oneOf` the
#: same way, and `minItems`/`minLength`/`minProperties` all as "should be non-empty".
KEYWORD_FAMILY = {
    "anyOf": "combinator",
    "oneOf": "combinator",
    "minItems": "non-empty",
    "minLength": "non-empty",
    "minProperties": "non-empty",
}

#: Per engine, (regex, family) tried in order against a schema problem's message. Each engine's
#: validator library words its messages differently; these name the failing keyword's family so
#: three engines are compared on *which* keyword failed, not only where.
MESSAGE_FAMILIES: dict[str, list[tuple[str, str]]] = {
    "python": [
        (r" is not of type ", "type"),
        (r" is a required property$", "required"),
        (r"^Additional properties are not allowed ", "additionalProperties"),
        (r" is not one of ", "enum"),
        (r" was expected$", "const"),
        (r" does not match ", "pattern"),
        (r" is less than the minimum of ", "minimum"),
        (r" is greater than the maximum of ", "maximum"),
        (
            r" (should be non-empty|is too short|does not have enough properties)$",
            "non-empty",
        ),
        (r" has non-unique elements$", "uniqueItems"),
        (r" is not valid under any of the given schemas$", "combinator"),
        (r" is valid under each of ", "combinator"),
    ],
    "typescript": [
        (r"^must be equal to one of the allowed values$", "enum"),
        (r"^must be equal to constant$", "const"),
        (r"^must be >= ", "minimum"),
        (r"^must be <= ", "maximum"),
        (r"^must be [a-z,]+$", "type"),
        (r"^must have required property ", "required"),
        (r"^must NOT have additional properties ", "additionalProperties"),
        (r"^must match pattern ", "pattern"),
        (r"^must NOT have fewer than 1 (items|characters|properties)$", "non-empty"),
        (r"^must NOT have duplicate items ", "uniqueItems"),
        (r"^must match (a schema in anyOf|exactly one schema in oneOf)$", "combinator"),
    ],
    "java": [
        (r": \w+ found, [\w, ]+ expected$", "type"),
        (r": required property '.*' not found$", "required"),
        (r" are not allowed \(additional properties ", "additionalProperties"),
        (r": does not have a value in the enumeration ", "enum"),
        (r": must be the constant value ", "const"),
        (r": does not match the regex pattern ", "pattern"),
        (r": must have a minimum value of ", "minimum"),
        (r": must have a maximum value of ", "maximum"),
        (r": must (have|be) at least 1 ", "non-empty"),
        (r": must have only unique items in the array$", "uniqueItems"),
        (
            r"(^does not match any of the required alternatives$|: must be valid to one and only one schema)",
            "combinator",
        ),
    ],
}

UNCLASSIFIED = "UNCLASSIFIED"


def message_family(engine: str, message: str) -> str:
    """The keyword family `engine`'s schema-problem `message` reports, or `UNCLASSIFIED`."""
    for pattern, family in MESSAGE_FAMILIES[engine]:
        if re.search(pattern, message):
            return family
    return UNCLASSIFIED


def problem_signatures(engine: str, problems: list[str]) -> list[str]:
    """Each problem's prefix (`problem_prefixes`) plus, for a schema problem, the failing keyword's
    family -- what the three engines must agree on, in order."""
    out = []
    for problem in problems:
        prefix, message = _split_schema_problem(problem)
        out.append(
            prefix
            if message is None
            else f"{prefix} [{message_family(engine, message)}]"
        )
    return out


def compare_labels(
    case: str, py: list[str], ts: list[str], java: list[str], failures: list[str]
) -> None:
    if not (py == ts == java):
        failures.append(
            f"{case}: problem labels disagree\n  python={py}\n  typescript={ts}\n  java={java}"
        )


def compare_signatures(
    case: str,
    py_env: dict[str, Any],
    ts_env: dict[str, Any],
    java_env: dict[str, Any],
    failures: list[str],
) -> None:
    """The three engines' `problem_signatures` must be identical, and every schema message must be
    one the engine's classifier knows (an unknown wording would otherwise compare as equal-unknown)."""
    signatures = {
        engine: problem_signatures(engine, env["problems"])
        for engine, env in (
            ("python", py_env),
            ("typescript", ts_env),
            ("java", java_env),
        )
    }
    compare_labels(
        case,
        signatures["python"],
        signatures["typescript"],
        signatures["java"],
        failures,
    )
    for engine, env in (("python", py_env), ("typescript", ts_env), ("java", java_env)):
        for problem, signature in zip(env["problems"], signatures[engine]):
            if signature.endswith(f"[{UNCLASSIFIED}]"):
                failures.append(
                    f"{case}: {engine} schema message not classified: {problem!r}"
                )


#: The label a real third-party standard's problems carry, by the census's schema name.
STAGE_LABELS = {
    "oscal-assessment-results-nist-1.1.2": NIST_LABEL,
    "sarif-2.1.0": SARIF_LABEL,
}


def census_cases(plan: dict[str, Any]) -> list[tuple[str, str, list[dict[str, Any]]]]:
    """The plan's mutations packed into as few report directories as possible: one mutation per
    artifact per directory (artifacts validate independently), as `(case, base, mutations)`."""
    by_base: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for mutation in plan["mutations"]:
        by_base.setdefault(mutation["base"], {}).setdefault(
            mutation["file"], []
        ).append(mutation)
    cases = []
    for base, files in by_base.items():
        for k in range(max(len(ms) for ms in files.values())):
            batch = [ms[k] for _, ms in sorted(files.items()) if k < len(ms)]
            cases.append((f"census-{base}-{k}", base, batch))
    return cases


def census_expectations(
    case: str,
    mutations: list[dict[str, Any]],
    py_env: dict[str, Any],
    failures: list[str],
) -> None:
    """Python (the reference) must report each mutation exactly where the census search said, under
    the keyword's family -- or, for `format`, report nothing for that file at all."""
    signatures = problem_signatures("python", py_env["problems"])
    for m in mutations:
        label = STAGE_LABELS.get(m["stage"], m["file"])
        pair = f"({m['stage']}, {m['keyword']})"
        if m["keyword"] == "format":
            if any(p.startswith(f"{m['file']}") for p in py_env["problems"]):
                failures.append(
                    f"{case}: {pair} must not be a problem: {py_env['problems']}"
                )
            continue
        family = KEYWORD_FAMILY.get(m["reported_as"], m["reported_as"])
        expected = f"{label}: {m['location']} [{family}]"
        if expected not in signatures:
            failures.append(
                f"{case}: {pair} expected {expected!r}, python gave {signatures}"
            )


def uncovered_failures(plan: dict[str, Any]) -> list[str]:
    """A census pair no mutation reached is a gap in this check, never a pass."""
    return [
        f"census pair ({schema}, {keyword}) has no mutation: no {keyword} failure was found in any base report"
        for schema, keyword in plan["uncovered"]
    ]


def run_census_cases(tmp: Path, base: Path, failures: list[str]) -> int:
    """Every census (schema, keyword) pair, failed once, compared across the three engines; returns
    the number of `--validate` invocations per engine."""
    bases = [base] + build_view_bases(tmp)
    plan = discover_census_mutations(bases)
    failures += uncovered_failures(plan)
    by_name = {b.name: b for b in bases}
    cases = census_cases(plan)
    for case, base_name, mutations in cases:
        shutil.copytree(by_name[base_name], tmp / case)
        for m in mutations:
            _write_json(tmp / case / m["file"], m["document"])
    # Each (directory, engine) run is an independent read-only subprocess: run them side by side.
    with ThreadPoolExecutor(max_workers=6) as pool:
        runs = {
            (case, engine): pool.submit(validate, tmp / case)
            for case, _, _ in cases
            for engine, validate in ENGINES
        }
    for case, _, mutations in cases:
        expect_exit = 0 if all(m["keyword"] == "format" for m in mutations) else 3
        envs = []
        for engine, _ in ENGINES:
            out, code = runs[(case, engine)].result()
            if code != expect_exit:
                failures.append(
                    f"{case}: {engine} exited {code}, expected {expect_exit}"
                )
            envs.append(json.loads(out))
        compare_signatures(case, envs[0], envs[1], envs[2], failures)
        census_expectations(case, mutations, envs[0], failures)
    return len(cases)


# --- The reader-case pass (SPEC 8.7 report-file reader contract, item 18.60) ---------------------

READER_CASES = ROOT / "spec" / "report" / "reader-cases.json"
SIGN_KEY = ROOT / "tools" / "fixtures" / "sign" / "test-key.pem"
FULL_EMIT = "md,html,oscal,sarif,pack,junit,csv,oscal_xml"
READER_ENGINES = ("python", "typescript", "java")
#: A reader-contract fault as every engine words it, file and message alike: the rule and its line.
READER_MARK = re.compile(
    r"outside the (?:JSON profile|report-file reader contract): [a-z0-9_]+ at line \d+"
)


def _ts_entry() -> Path:
    entry = TS_ENGINE / "dist" / "cli.js"
    if not entry.is_file():
        raise SystemExit(
            f"typescript dist is not built: {entry} is missing "
            "(run `pnpm build` in engines/typescript first)"
        )
    return entry


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


def engine_argv(engine: str) -> list[str]:
    """The command that runs `engine`'s own CLI: Python's console script, the built TypeScript
    `dist/cli.js`, the built Java runnable jar."""
    if engine == "python":
        return ["uv", "run", "--frozen", "--project", str(PY_ENGINE), "agentce"]
    if engine == "typescript":
        return ["node", str(_ts_entry())]
    return ["java", "-jar", str(_java_jar())]


def run_engine(engine: str, args: list[str]) -> tuple[str, int]:
    """One engine command with `LC_ALL=C.UTF-8` and no inherited `AGENTCE_OPERATOR` or `VIRTUAL_ENV`."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("AGENTCE_OPERATOR", "VIRTUAL_ENV")
    }
    env["LC_ALL"] = "C.UTF-8"
    proc = subprocess.run(
        [*engine_argv(engine), *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
        env=env,
    )
    return proc.stdout, proc.returncode


#: The --emit outputs an engine that cannot write --emit yet (TypeScript and Java until 18.16b,
#: `input.emit_unsupported`) gets from Python's full base, so every case's file is in its folder.
EMIT_ONLY = ("report.csv", "report.junit.xml", "oscal-ar.xml")


def build_engine_base(
    engine: str, directory: Path, python_base: Path | None = None
) -> Path:
    """`engine`'s own `assess` with every `--emit` token over the quickstart project (the arguments
    `build_base_report` gives Python's), so each case edits a folder the engine under test wrote.
    An engine that refuses `--emit` with `input.emit_unsupported` writes its core outputs instead
    and gets `EMIT_ONLY` copied from `python_base` (each engine's validate reads those files
    whenever they are present)."""
    out = directory / f"base-{engine}"
    core = ["assess", *QUICKSTART_ARGS, "--out", str(out)]
    stdout, code = run_engine(engine, [*core, "--emit", FULL_EMIT, "--json"])
    if code == 3 and python_base is not None:
        answer = answer_texts("assess", stdout)
        if answer is not None and answer[0] == "input.emit_unsupported":
            shutil.rmtree(out, ignore_errors=True)
            stdout, code = run_engine(engine, core)
            if code in (0, 1, 2):
                for name in EMIT_ONLY:
                    if not (out / name).exists():
                        shutil.copyfile(python_base / name, out / name)
    if code not in (0, 1, 2):
        raise SystemExit(
            f"{engine}: building the reader-case base failed (exit {code}):\n{stdout}"
        )
    return out


def load_reader_cases(path: Path = READER_CASES) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))["cases"]
    return cases


def apply_edits(data: bytes | None, edits: list[dict[str, Any]]) -> bytes | None:
    """The case's edits applied in order to a file's bytes (None: the file does not exist); None
    back when an edit cannot apply (no such file, or `find_hex` not in it)."""
    for edit in edits:
        content = bytes.fromhex(edit["hex"])
        op = edit["op"]
        if op == "write":
            data = content
            continue
        if data is None:
            return None
        if op == "prepend":
            data = content + data
        elif op == "append":
            data = data + content
        else:
            find = bytes.fromhex(edit["find_hex"])
            if find not in data:
                return None
            data = data.replace(find, content, 1 if op == "replace" else -1)
    return data


def snapshot(directory: Path) -> dict[str, bytes | None]:
    """Every path under `directory`, mapped to its bytes (None for a folder)."""
    return {
        p.relative_to(directory).as_posix(): (None if p.is_dir() else p.read_bytes())
        for p in sorted(directory.rglob("*"))
    }


def changed_paths(
    before: dict[str, bytes | None], after: dict[str, bytes | None]
) -> list[str]:
    return sorted(
        path
        for path in before.keys() | after.keys()
        if before.get(path, b"\0absent") != after.get(path, b"\0absent")
    )


def reader_command(case: dict[str, Any], case_dir: Path, base: Path) -> list[str]:
    """The argv of the case's command: `report --validate`, `readiness`, `sign` with the kms test
    key (a clean report signs with exit 0 in every engine), or `diff` of the base's file against the
    edited one."""
    command = case["command"]
    if command == "report-validate":
        return ["report", "--validate", str(case_dir), "--json"]
    if command == "readiness":
        return ["readiness", str(case_dir), "--json"]
    if command == "sign":
        return [
            "sign",
            "--json",
            str(case_dir),
            "--as",
            "claimant",
            "--profile",
            "kms",
            "--key",
            str(SIGN_KEY),
        ]
    if command == "diff":
        return [
            "diff",
            str(base / case["file"]),
            str(case_dir / case["file"]),
            "--json",
        ]
    raise SystemExit(f"reader case {case['id']}: unknown command {command!r}")


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def answer_texts(command: str, out: str) -> tuple[Any, list[str]] | None:
    """(message key, texts) of a refusal: `report --validate`'s top-level `message_key` and problem
    lines, or an input error's key (Python `error.key`, TypeScript and Java `error.message_key`) and
    every text of its error object. None when the output is no `--json` envelope."""
    try:
        env = json.loads(out)
    except json.JSONDecodeError:
        return None
    if not isinstance(env, dict):
        return None
    if command == "report-validate":
        return env.get("message_key"), _strings(env.get("problems"))
    error = env.get("error")
    if not isinstance(error, dict):
        return None, []
    return error.get("key", error.get("message_key")), _strings(error)


def judge_reader_case(
    case: dict[str, Any], engine: str, out: str, code: int, changed: list[str]
) -> list[str]:
    """One engine's answer to one case against the table: exit, then on a refusal the key, the
    `names` text in a problem line naming the file (validate) or in the error, and nothing written."""
    where = f"reader {case['id']}: {engine}"
    if code != case["exit"]:
        return [
            f"{where}: exit {code}, expected {case['exit']} ({out.strip()[:300]!r})"
        ]
    if code != 3:
        return []
    answer = answer_texts(case["command"], out)
    if answer is None:
        return [f"{where}: exit 3 with no --json envelope ({out.strip()[:300]!r})"]
    key, texts = answer
    problems: list[str] = []
    if key != case["key"]:
        problems.append(f"{where}: key {key!r}, expected {case['key']!r}")
    named = [t for t in texts if case["names"] in t]
    if not named:
        problems.append(f"{where}: nothing names {case['names']!r}: {texts}")
    elif case["command"] == "report-validate" and not any(
        t.startswith(case["file"]) for t in named
    ):
        problems.append(
            f"{where}: the line naming {case['names']!r} does not name {case['file']}: {named}"
        )
    if changed:
        what = (
            "signatures/ or claim.json" if case["command"] == "sign" else "the folder"
        )
        problems.append(
            f"{where}: a refused {case['command']} changed {what}: {changed}"
        )
    return problems


def compare_reader_marks(
    case: dict[str, Any], outs: dict[str, tuple[str, int]]
) -> list[str]:
    """When every engine refused, each names the same reader-contract rules at the same lines."""
    if any(code != 3 for _, code in outs.values()):
        return []
    marks = {}
    for engine, (out, _) in outs.items():
        answer = answer_texts(case["command"], out)
        texts = answer[1] if answer else []
        marks[engine] = sorted(m for t in texts for m in READER_MARK.findall(t))
    if len({json.dumps(v) for v in marks.values()}) > 1:
        return [
            f"reader {case['id']}: the engines name different rules or lines: {marks}"
        ]
    return []


def run_reader_case(
    case: dict[str, Any], engine: str, base: Path, work: Path
) -> tuple[str, int, list[str]]:
    """Copy `engine`'s base, apply the case's edits, run its command in `engine`; (stdout, exit,
    paths the run changed). Exit -1 when the edits do not apply to this engine's base."""
    case_dir = work / engine / case["id"]
    shutil.copytree(base, case_dir)
    target = case_dir / case["file"]
    edited = apply_edits(
        target.read_bytes() if target.is_file() else None, case["edits"]
    )
    if edited is None:
        return f"the edits do not apply to {engine}'s {case['file']}", -1, []
    target.write_bytes(edited)
    before = snapshot(case_dir)
    out, code = run_engine(engine, reader_command(case, case_dir, base))
    return out, code, changed_paths(before, snapshot(case_dir))


def reader_happy_paths(
    bases: dict[str, Path], work: Path, pool: ThreadPoolExecutor
) -> list[str]:
    """Each engine's clean base validates 0 in all three engines, reads READY and signs (exit 0)
    in its own engine."""
    failures: list[str] = []
    validate = {
        (writer, reader): pool.submit(
            run_engine, reader, ["report", "--validate", str(base), "--json"]
        )
        for writer, base in bases.items()
        for reader in READER_ENGINES
    }
    readiness: dict[str, tuple[Path, Any]] = {}
    sign: dict[str, tuple[Path, Any]] = {}
    for engine, base in bases.items():
        for name, store in (("readiness", readiness), ("sign", sign)):
            copy = work / engine / f"happy-{name}"
            shutil.copytree(base, copy)
            case = {"command": name, "file": "assertions.json"}
            store[engine] = (
                copy,
                pool.submit(run_engine, engine, reader_command(case, copy, base)),
            )
    for (writer, reader), job in validate.items():
        out, code = job.result()
        if code != 0:
            failures.append(
                f"reader happy path: {writer}'s report validated by {reader} exit {code} ({out.strip()[:300]!r})"
            )
    for engine, (copy, job) in readiness.items():
        out, code = job.result()
        verdict = None
        if code == 0:
            try:
                verdict = json.loads(out).get("verdict")
            except (json.JSONDecodeError, AttributeError):
                pass
        if code != 0 or verdict != "READY":
            failures.append(
                f"reader happy path: {engine} readiness exit {code}, verdict {verdict!r}, expected 0 READY"
            )
    for engine, (copy, job) in sign.items():
        out, code = job.result()
        if code != 0 or not (copy / "signatures").is_dir():
            failures.append(
                f"reader happy path: {engine} sign exit {code}, expected 0 and a signature ({out.strip()[:300]!r})"
            )
    return failures


def run_reader_cases(tmp: Path, failures: list[str]) -> int:
    """Every spec/report/reader-cases.json case in every engine, each on a copy of the full report
    that engine wrote; returns the number of case runs."""
    cases = load_reader_cases()
    work = tmp / "reader"
    with ThreadPoolExecutor(max_workers=max(3, min(8, os.cpu_count() or 4))) as pool:
        python_base = build_engine_base("python", tmp)
        built = {
            e: pool.submit(build_engine_base, e, tmp, python_base)
            for e in READER_ENGINES
            if e != "python"
        }
        bases = {"python": python_base, **{e: job.result() for e, job in built.items()}}
        jobs = {
            (case["id"], engine): pool.submit(
                run_reader_case, case, engine, bases[engine], work
            )
            for case in cases
            for engine in READER_ENGINES
        }
        failures += reader_happy_paths(bases, work, pool)
    for case in cases:
        outs: dict[str, tuple[str, int]] = {}
        for engine in READER_ENGINES:
            out, code, changed = jobs[(case["id"], engine)].result()
            if code == -1:
                failures.append(f"reader {case['id']}: {out}")
                continue
            outs[engine] = (out, code)
            failures += judge_reader_case(case, engine, out, code, changed)
        if len(outs) == len(READER_ENGINES):
            failures += compare_reader_marks(case, outs)
    return len(jobs)


def reader_self_test(failures: list[str]) -> None:
    """The reader pass's comparison, over the real case table and synthetic engine answers (no
    build): an answer that matches each case passes; the same answer judged against the case with its
    exit flipped fails; an fake engine that always answers valid fails every refusal case; a refusal
    that changed the folder, and engines naming different lines, fail."""
    cases = load_reader_cases()

    def ideal(case: dict[str, Any]) -> tuple[str, int]:
        if case["exit"] == 0:
            return json.dumps({"valid": True, "problems": []}), 0
        if case["command"] == "report-validate":
            line = f"{case['file']}: {case['names']}"
            body = {"valid": False, "message_key": case["key"], "problems": [line]}
        elif case["command"] == "readiness":  # Python's envelope field names
            body = {"error": {"key": case["key"], "cause": f"x {case['names']} y"}}
        else:  # TypeScript and Java's
            body = {"error": {"message_key": case["key"], "detail": case["names"]}}
        return json.dumps(body), 3

    always_valid = (json.dumps({"valid": True, "problems": []}), 0)
    for case in cases:
        out, code = ideal(case)
        got = judge_reader_case(case, "ideal", out, code, [])
        if got:
            failures.append(f"reader judge refused a matching answer: {got}")
        flipped = {**case, "exit": 0 if case["exit"] == 3 else 3}
        if not judge_reader_case(flipped, "ideal", out, code, []):
            failures.append(
                f"reader judge missed case {case['id']} with its exit flipped"
            )
        if case["exit"] == 3 and not judge_reader_case(
            case, "always-valid", *always_valid, []
        ):
            failures.append(
                f"reader judge let an always-valid engine pass {case['id']}"
            )
    refusals = [c for c in cases if c["exit"] == 3]
    if not refusals:
        failures.append("the reader case table holds no refusal case")
        return
    sign = next((c for c in refusals if c["command"] == "sign"), refusals[0])
    out, code = ideal(sign)
    if not judge_reader_case(
        sign, "ideal", out, code, ["signatures/claimant-kms.dsse.json"]
    ):
        failures.append("reader judge missed a refused sign that wrote a signature")
    csv = next(c for c in refusals if c["rule"] == "csv_bare_cr")
    side_effect = json.dumps(
        {
            "valid": False,
            "message_key": csv["key"],
            "problems": ["report.csv: missing column 'mode'"],
        }
    )
    if not judge_reader_case(csv, "side-effect", side_effect, 3, []):
        failures.append(
            "reader judge let a refusal for the wrong reason (a missing column) pass"
        )
    wrong_key = {**sign, "key": "some.other_key"}
    if not judge_reader_case(wrong_key, "ideal", out, code, []):
        failures.append("reader judge missed a refusal under the wrong key")

    def line(n: int) -> str:
        return json.dumps(
            {
                "valid": False,
                "message_key": "report.validation_failed",
                "problems": [
                    f"report.csv: outside the report-file reader contract: csv_bare_cr at line {n} (SPEC 8.7)"
                ],
            }
        )

    probe = {"id": "probe", "command": "report-validate"}
    same = {e: (line(2), 3) for e in READER_ENGINES}
    if compare_reader_marks(probe, same):
        failures.append("reader mark comparison refused three identical answers")
    if not compare_reader_marks(probe, {**same, "java": (line(3), 3)}):
        failures.append("reader mark comparison missed engines naming different lines")
    edits = [
        {"op": "prepend", "hex": "efbbbf"},
        {"op": "replace", "find_hex": "0a", "hex": "0d0a"},
        {"op": "append", "hex": "21"},
    ]
    if apply_edits(b"a\nb\n", edits) != b"\xef\xbb\xbfa\r\nb\n!":
        failures.append("apply_edits: prepend, replace and append went wrong")
    if (
        apply_edits(b"a\nb\n", [{"op": "replace_all", "find_hex": "0a", "hex": "0d"}])
        != b"a\rb\r"
    ):
        failures.append("apply_edits: replace_all went wrong")
    if apply_edits(b"a", [{"op": "replace", "find_hex": "7a", "hex": ""}]) is not None:
        failures.append("apply_edits: a replace whose text is absent was applied")
    if apply_edits(None, [{"op": "write", "hex": "7b"}]) != b"{":
        failures.append("apply_edits: write did not create the file")


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
        problem_prefixes(
            [f"results.sarif (OASIS SARIF 2.1.0): {region}: combinator message"]
        ),
        problem_prefixes(
            [
                f"results.sarif (OASIS SARIF 2.1.0): {region}: branch 1",
                f"results.sarif (OASIS SARIF 2.1.0): {region}: branch 2",
            ]
        ),
        problem_prefixes(
            [f"results.sarif (OASIS SARIF 2.1.0): {region}: combinator message"]
        ),
        location_count_divergence,
    )
    if not location_count_divergence:
        failures.append(
            "comparator failed to catch a same-location problem-count divergence between engines"
        )

    # Each engine's real wording for every keyword family (captured from the census cases' own
    # output), so a classifier that drifts from what an engine actually prints fails here.
    real_messages = {
        "python": {
            "type": "'x x' is not of type 'object'",
            "required": "'control' is a required property",
            "additionalProperties": "Additional properties are not allowed ('zz_unexpected' was unexpected)",
            "enum": "'x x' is not one of ['automated', 'semi-automated', 'manual']",
            "const": "'2.1.0' was expected",
            "pattern": "'x x' does not match '^[A-Z]{2,4}$'",
            "minimum": "-1 is less than the minimum of 0",
            "maximum": "1000000000 is greater than the maximum of 4",
            "non-empty": "[] should be non-empty",
            "uniqueItems": "[{'id': 'a'}, {'id': 'a'}] has non-unique elements",
            "combinator": "{} is not valid under any of the given schemas",
        },
        "typescript": {
            "type": "must be object",
            "required": "must have required property 'control'",
            "additionalProperties": "must NOT have additional properties ('zz_unexpected')",
            "enum": "must be equal to one of the allowed values",
            "const": "must be equal to constant",
            "pattern": 'must match pattern "^[A-Z]{2,4}$"',
            "minimum": "must be >= 0",
            "maximum": "must be <= 4",
            "non-empty": "must NOT have fewer than 1 items",
            "uniqueItems": "must NOT have duplicate items (items ## 0 and 1 are identical)",
            "combinator": "must match exactly one schema in oneOf",
        },
        "java": {
            "type": "$: string found, object expected",
            "required": "$[0]: required property 'control' not found",
            "additionalProperties": "properties 'zz_unexpected' are not allowed (additional properties are not allowed)",
            "enum": '$[0].mode: does not have a value in the enumeration ["automated", "semi-automated", "manual"]',
            "const": "$.version: must be the constant value '2.1.0'",
            "pattern": "$[0].family: does not match the regex pattern ^[A-Z]{2,4}$",
            "minimum": "$.counts.conformant: must have a minimum value of 0",
            "maximum": "$[0].rung: must have a maximum value of 4",
            "non-empty": "$.runs: must have at least 1 items but found 0",
            "uniqueItems": "$.runs[0].tool.driver.rules: must have only unique items in the array",
            "combinator": "does not match any of the required alternatives",
        },
    }
    for engine, by_family in real_messages.items():
        for family, message in by_family.items():
            got = message_family(engine, message)
            if got != family:
                failures.append(
                    f"{engine} classifier: {message!r} gave {got!r}, expected {family!r}"
                )
        if message_family(engine, "an engine's newly worded message") != UNCLASSIFIED:
            failures.append(f"{engine} classifier accepted a message it has never seen")

    # Same location, different failing keyword: the location-only comparison called this a match.
    unclassified: list[str] = []
    compare_signatures(
        "self-test-unclassified",
        {"problems": ["assertions.json: 0: 'control' is a required property"]},
        {"problems": ["assertions.json: 0: must have required property 'control'"]},
        {"problems": ["assertions.json: 0: a wording no classifier knows"]},
        unclassified,
    )
    if not any("not classified" in f for f in unclassified):
        failures.append("an unclassified schema message was not reported as a failure")
    family_divergence: list[str] = []
    compare_signatures(
        "self-test-family-divergence",
        {"problems": ["assertions.json: 0/rung: -1 is less than the minimum of 0"]},
        {"problems": ["assertions.json: 0/rung: must be <= 4"]},
        {
            "problems": [
                "assertions.json: 0/rung: $[0].rung: must have a minimum value of 0"
            ]
        },
        family_divergence,
    )
    if not family_divergence:
        failures.append(
            "comparator missed two engines failing different keywords at one location"
        )

    # A census pair the mutation search could not reach must fail the check, never pass silently.
    if not uncovered_failures({"mutations": [], "uncovered": [["buyer", "const"]]}):
        failures.append("an uncovered census pair was not reported as a failure")
    packed = census_cases(
        {
            "mutations": [
                {"base": "full", "file": "a.json", "keyword": "type"},
                {"base": "full", "file": "a.json", "keyword": "enum"},
                {"base": "full", "file": "b.json", "keyword": "type"},
            ],
            "uncovered": [],
        }
    )
    if [(case, len(batch)) for case, _, batch in packed] != [
        ("census-full-0", 2),
        ("census-full-1", 1),
    ]:
        failures.append(
            f"census mutations packed wrongly (two in one file must split): {packed}"
        )

    reader_self_test(failures)

    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if not failures:
        print(
            "report_validate_parity_check self-test: comparator, label extraction, keyword "
            "classifiers and the reader-case judge discriminate"
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
            keys = [env.get("message_key") for env in (py_env, ts_env, java_env)]
            if keys != [REPORT_INVALID if expect_exit else None] * 3:
                failures.append(
                    f"{case}: message_key (python, typescript, java) was {keys}"
                )
            if not (py_env["valid"] == ts_env["valid"] == java_env["valid"]):
                failures.append(
                    f"{case}: 'valid' disagrees "
                    f"(python={py_env['valid']}, typescript={ts_env['valid']}, java={java_env['valid']})"
                )

            py_labels = problem_labels(py_env["problems"])
            ts_labels = problem_labels(ts_env["problems"])
            java_labels = problem_labels(java_env["problems"])
            compare_signatures(case, py_env, ts_env, java_env, failures)

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

        census_count = run_census_cases(tmp, base, failures)
        read_count = run_read_cases(tmp, base, failures)
        reader_count = run_reader_cases(tmp, failures)

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
    print(
        f"MATCH: {len(CASES) + 1 + read_count + census_count} invocations agree across python, typescript, java "
        f"({census_count} cover every census (schema, keyword) pair); {reader_count} reader-case runs "
        "match spec/report/reader-cases.json"
    )
    return 0


def run_reader_check() -> int:
    """The reader-case pass alone."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="report-reader-parity-") as raw:
        count = run_reader_cases(Path(raw), failures)
    for failure in failures:
        print(f"MISMATCH: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(
        f"MATCH: {count} reader-case runs match spec/report/reader-cases.json in python, typescript, java"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="report_validate_parity_check", description=__doc__.split("\n")[0]
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument(
        "--reader-cases",
        action="store_true",
        help="run only the spec/report/reader-cases.json pass",
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.reader_cases:
        return run_reader_check()
    return run_real_check()


if __name__ == "__main__":
    raise SystemExit(main())
