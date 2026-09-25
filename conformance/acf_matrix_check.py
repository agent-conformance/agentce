"""Repository check: `conformance/acf/matrix.yaml` validates against its own schema, carries exactly the
36 named capabilities of the Agent Conformance Framework's seven dimensions (no more, no fewer, no wrong
name), and every entry id is unique. `--check-fixtures` additionally proves every entry's `test` actually
runs: its `fixture` path (when set) exists, its `cmd` reproduces the same outcome on two separate runs
(catching a flaky or order-dependent command), and that outcome matches a committed golden record --
`conformance/acf/fixtures-golden.json` -- so a capability's proof can never be "fixed" by quietly recording
its current, broken behaviour as the new golden (`check_golden_no_failures` rejects a golden that bakes in
a non-zero exit or a missing `expect` match).

Every rejection carries a stable `key` (this script's own convention, mirroring
`tools/workflow_pins_check.py`'s discriminating self-test) so a caller can tell which structural rule
failed, not just that validation failed, and so a later caller can match on the key rather than parsing
prose.

The 36-capability canonical list below is hardcoded, not parsed from the framework document, because
that document lives outside this repository's tracked tree and this check must also run correctly from a
plain public clone (public CI, a verifier's clean clone, an end user's checkout) that never has it
present. This file never names that document's location: `check_framework_doc_sync` reads it only from
an environment-variable override, unset by default, so a plain clone's tracked source carries no
reference to a companion tree it does not have. When a caller (a maintainer's own working copy) sets the
override, that half additionally confirms the canonical list has not drifted from the document's wording;
otherwise it is silently skipped, matching this codebase's existing pattern for optional local-only
checks (see `conformance/governance_check.py`'s `PRIVATE_TERMS` handling).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import jsonschema
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_MATRIX = _ROOT / "conformance" / "acf" / "matrix.yaml"
_SCHEMA = _ROOT / "conformance" / "acf" / "matrix.schema.json"
_GOLDEN = _ROOT / "conformance" / "acf" / "fixtures-golden.json"
_FRAMEWORK_DOC_ENV = "AGENTCE_ACF_FRAMEWORK_DOC"
_DEFAULT_CMD_TIMEOUT_S = 300


def _framework_doc_path() -> Path | None:
    """The companion framework document's path, from an environment-variable override only -- never a
    hardcoded default -- so this tracked file names no path outside the repository."""
    override = os.environ.get(_FRAMEWORK_DOC_ENV)
    return Path(override) if override else None


_SEVEN_DIMENSIONS = (
    "coverage",
    "rigor",
    "interoperability",
    "lifecycle",
    "operability",
    "assurance",
    "governance",
)

# The exact (dimension, capability) pairs of the ACF's 36 named capabilities (the framework document's
# "The seven dimensions" section, in that document's own order and wording). A matrix that does not
# carry exactly this set -- not a superset, not a subset, not a
# same-count-different-name set -- fails validation: this is what stops a placeholder matrix (e.g. one
# entry per dimension with interchangeable filler text) from ever passing.
_CANONICAL_CAPABILITIES: tuple[tuple[str, str], ...] = (
    ("coverage", "Standards & rulesets"),
    ("coverage", "Agent frameworks / runtimes"),
    ("coverage", "Languages / SDKs"),
    ("coverage", "Evidence sources / ingestion"),
    ("coverage", "Agent modalities"),
    ("rigor", "Evaluation depth"),
    ("rigor", "Determinism (order/locale/clock-independent)"),
    ("rigor", "Model-free (no learned component decides an outcome)"),
    ("rigor", "Insufficient-evidence honesty"),
    ("rigor", "Provenance & signing"),
    ("rigor", "Self-proof (the evaluator proves itself)"),
    ("interoperability", "Output formats (machine/human/GRC/CI)"),
    ("interoperability", "CI/CD surface"),
    ("interoperability", "Policy-as-code gating"),
    ("interoperability", "Downstream & GRC consumers"),
    ("interoperability", "AI-native consumption"),
    ("lifecycle", "Pre-production assessment"),
    ("lifecycle", "Runtime / continuous conformance"),
    ("lifecycle", "Run-to-run delta"),
    ("lifecycle", "Remediation & re-verify"),
    ("lifecycle", "Retention & audit trail"),
    ("operability", "Offline / self-hosted"),
    ("operability", "Air-gapped"),
    ("operability", "Data residency & minimization"),
    ("operability", "Scale & performance at realistic deployment sizes"),
    ("assurance", "Tested and bug-free (real CI: lint, type, tests, coverage)"),
    (
        "assurance",
        "Secure supply chain (SBOM, VEX, signed & reproducible releases, SHA-pinned actions)",
    ),
    ("assurance", "A maintained threat model"),
    ("assurance", "No-network guarantee proven, not asserted"),
    ("assurance", "Vulnerability disclosure & response"),
    ("assurance", "Support, versioning & stability policy"),
    ("assurance", "Clear licensing"),
    ("governance", "A neutral home (foundation / working group)"),
    ("governance", "A conformance program & marks"),
    ("governance", "Extensibility (extension points, not forks — CP-6)"),
    (
        "governance",
        "A contributor ecosystem (external PRs, third-party catalogs/adapters/formats as contributions)",
    ),
)
assert len(_CANONICAL_CAPABILITIES) == 36
assert {d for d, _ in _CANONICAL_CAPABILITIES} == set(_SEVEN_DIMENSIONS)


def load_schema() -> dict[str, Any]:
    return json.loads(_SCHEMA.read_text(encoding="utf-8"))


def load_matrix(path: Path = _MATRIX) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("matrix.yaml: top level must be a mapping")
    return data


class Problem:
    __slots__ = ("detail", "key")

    def __init__(self, key: str, detail: str) -> None:
        self.key = key
        self.detail = detail

    def __repr__(self) -> str:
        return f"{self.key}: {self.detail}"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Problem) and (self.key, self.detail) == (
            other.key,
            other.detail,
        )


def validate(data: dict[str, Any], schema: dict[str, Any]) -> list[Problem]:
    """Return a list of Problems; empty means the matrix is structurally valid."""
    problems: list[Problem] = []

    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)
    for error in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in error.path) or "<root>"
        problems.append(Problem("matrix.schema_invalid", f"{loc}: {error.message}"))

    if problems:
        # A schema-invalid document may not have the shape the structural checks below assume.
        return problems

    entries = data.get("entries", [])
    seen_ids: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    for entry in entries:
        entry_id = entry.get("id")
        if entry_id in seen_ids:
            problems.append(Problem("matrix.duplicate_id", repr(entry_id)))
        seen_ids.add(entry_id)
        seen_pairs.add((entry.get("dimension"), entry.get("capability")))

    canonical = set(_CANONICAL_CAPABILITIES)
    missing = sorted(canonical - seen_pairs)
    extra = sorted(seen_pairs - canonical)
    if missing:
        problems.append(Problem("matrix.capability_missing", repr(missing)))
    if extra:
        problems.append(Problem("matrix.capability_unrecognized", repr(extra)))

    return problems


_MD_STRIP_RE = re.compile(r"[*_`]")
_MD_NONWORD_RE = re.compile(r"[^a-z0-9 ]+")


_WHITESPACE_RE = re.compile(r" +")


def _normalize(text: str) -> str:
    stripped = _MD_NONWORD_RE.sub(" ", _MD_STRIP_RE.sub("", text.lower()))
    return _WHITESPACE_RE.sub(" ", stripped).strip()


def check_framework_doc_sync() -> list[str]:
    """Best-effort: when the private framework document is present, confirm the hardcoded canonical
    capability list has not drifted from its wording. Returns problem strings; empty (including when
    the document is absent) means either "in sync" or "not checkable here"."""
    doc_path = _framework_doc_path()
    if doc_path is None or not doc_path.exists():
        return []
    text = doc_path.read_text(encoding="utf-8")
    start = text.find("## The seven dimensions")
    if start == -1:
        return ["framework doc present but 'The seven dimensions' heading not found"]
    section = text[start:]
    next_heading = re.search(r"\n## (?!The seven dimensions)", section[1:])
    if next_heading:
        section = section[: next_heading.start() + 1]
    normalized_section = _normalize(section)

    problems: list[str] = []
    for _dimension, capability in _CANONICAL_CAPABILITIES:
        # Compare only the part before any parenthetical -- the doc and this list format asides
        # slightly differently (e.g. "no data leaves" vs "(no data leaves)").
        core = capability.split("(")[0].strip()
        if _normalize(core) not in normalized_section:
            problems.append(f"capability not found in framework doc: {capability!r}")
    return problems


def load_golden(path: Path = _GOLDEN) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class Outcome:
    """The {exit, expect_ok} signal a `test.cmd` run produces -- exactly the pass/fail proof the
    matrix schema's own `test` field description defines, carrying no wall-clock-derived data (no
    duration, no timestamp), so two runs of a genuinely deterministic command always match."""

    __slots__ = ("exit", "expect_ok")

    def __init__(self, exit_code: int, expect_ok: bool | None) -> None:
        self.exit = exit_code
        self.expect_ok = expect_ok

    def as_dict(self) -> dict[str, Any]:
        return {"exit": self.exit, "expect_ok": self.expect_ok}

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Outcome) and (self.exit, self.expect_ok) == (
            other.exit,
            other.expect_ok,
        )

    def __repr__(self) -> str:
        return f"Outcome(exit={self.exit}, expect_ok={self.expect_ok})"


def run_entry_test(
    test: dict[str, Any], *, cwd: Path, timeout_s: int = _DEFAULT_CMD_TIMEOUT_S
) -> Outcome:
    """Run one matrix entry's `test.cmd` as the schema describes: one shell command, from the
    repository root (or a caller-chosen root for self-test fixtures)."""
    proc = subprocess.run(
        test["cmd"],
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    expect = test.get("expect")
    if expect is None:
        expect_ok = None
    else:
        expect_ok = expect in (proc.stdout + proc.stderr)
    return Outcome(proc.returncode, expect_ok)


def check_golden_no_failures(golden: dict[str, Any]) -> list[Problem]:
    """A committed golden record may never bake in a failure -- that would let a broken `test.cmd` be
    "fixed" by recording its current broken behaviour as the new expected outcome instead of fixing it."""
    problems: list[Problem] = []
    for entry_id, record in golden.items():
        if record.get("exit") != 0 or record.get("expect_ok") is False:
            problems.append(
                Problem("matrix.golden_records_failure", f"{entry_id}: {record!r}")
            )
    return problems


def check_fixtures(
    matrix: dict[str, Any],
    golden: dict[str, Any],
    *,
    root: Path,
    timeout_s: int = _DEFAULT_CMD_TIMEOUT_S,
) -> list[Problem]:
    """For every entry with a `test`: its `fixture` path (when set) exists under `root`; its `cmd`
    reproduces the identical {exit, expect_ok} outcome on two separate runs (catching a flaky or
    order-dependent command -- the check's proof of the standard's order/locale/clock determinism
    invariant, applied to the outcome a reader actually cares about rather than to incidental raw output
    such as a pytest timing line, which is never part of `Outcome` and so can never cause a false
    mismatch); and that outcome matches the committed golden record (catching drift -- a capability that
    silently regressed, or a golden that has gone stale)."""
    problems = check_golden_no_failures(golden)
    already_flagged = {p.detail.split(":", 1)[0] for p in problems}

    entries = matrix.get("entries", [])
    for entry in entries:
        test = entry.get("test")
        if test is None:
            continue
        entry_id = entry["id"]
        if entry_id in already_flagged:
            # Already rejected by check_golden_no_failures -- one problem per entry, naming the
            # most specific cause, not a cascade of consequential mismatches.
            continue

        fixture = test.get("fixture")
        if fixture is not None and not (root / fixture).exists():
            problems.append(
                Problem("matrix.fixture_missing", f"{entry_id}: {fixture!r}")
            )
            continue

        try:
            run1 = run_entry_test(test, cwd=root, timeout_s=timeout_s)
            run2 = run_entry_test(test, cwd=root, timeout_s=timeout_s)
        except subprocess.TimeoutExpired:
            problems.append(
                Problem("matrix.test_timeout", f"{entry_id}: exceeded {timeout_s}s")
            )
            continue

        if run1 != run2:
            problems.append(
                Problem(
                    "matrix.nondeterministic",
                    f"{entry_id}: run1={run1!r} run2={run2!r}",
                )
            )
            continue

        golden_record = golden.get(entry_id)
        if golden_record is None:
            problems.append(Problem("matrix.golden_missing", entry_id))
            continue

        golden_outcome = Outcome(golden_record["exit"], golden_record["expect_ok"])
        if run1 != golden_outcome:
            problems.append(
                Problem(
                    "matrix.golden_mismatch",
                    f"{entry_id}: golden={golden_outcome!r} actual={run1!r}",
                )
            )

    return problems


def record_golden(
    matrix: dict[str, Any], *, root: Path, timeout_s: int = _DEFAULT_CMD_TIMEOUT_S
) -> dict[str, Any]:
    """Run every entry's `test.cmd` once and return the {id: {exit, expect_ok}} golden record. A
    maintenance helper only (`--record-golden`) -- its output is committed by hand and never
    regenerated at eval or CI time; `check_fixtures` is what verifies against the committed file."""
    golden: dict[str, Any] = {}
    for entry in matrix.get("entries", []):
        test = entry.get("test")
        if test is None:
            continue
        golden[entry["id"]] = run_entry_test(
            test, cwd=root, timeout_s=timeout_s
        ).as_dict()
    return golden


def _fixture_case_matrix(
    entry_id: str, cmd: str, fixture: str | None, expect: str | None
) -> dict[str, Any]:
    return {
        "version": 1,
        "entries": [
            {
                "id": entry_id,
                "dimension": "coverage",
                "capability": "Self-test capability",
                "statement": "Self-test only.",
                "level_target": 3,
                "test": {"cmd": cmd, "fixture": fixture, "expect": expect},
                "evidence_ref": "self-test",
            }
        ],
    }


def _self_test_fixtures() -> list[str]:
    """Proves `check_fixtures` actually discriminates: a missing fixture path, a command whose
    outcome differs between two back-to-back runs, a golden that no longer matches real behaviour, a
    golden with no record at all, and a golden that bakes in a failure are each rejected; a genuinely
    deterministic, matching entry passes. Uses synthetic entries in a throwaway tempdir, never the
    real matrix -- so this self-test carries its own fixtures and never touches the repository."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "real_fixture").mkdir()

        counter = root / "counter-nondeterministic"
        counter.write_text("0", encoding="utf-8")
        flip_cmd = (
            f'python3 -c "import pathlib; p = pathlib.Path({str(counter)!r}); '
            'n = int(p.read_text()); p.write_text(str(n + 1)); raise SystemExit(n % 2)"'
        )

        cases: dict[str, dict[str, Any]] = {
            "deterministic entry, fixture present, golden matches": {
                "matrix": _fixture_case_matrix(
                    "SELF-1", "python3 -c \"print('ok')\"", "real_fixture", "ok"
                ),
                "golden": {"SELF-1": {"exit": 0, "expect_ok": True}},
                "should_fail": False,
            },
            "fixture path does not exist": {
                "matrix": _fixture_case_matrix(
                    "SELF-2", "true", "does/not/exist", None
                ),
                "golden": {"SELF-2": {"exit": 0, "expect_ok": None}},
                "should_fail": True,
            },
            "command's outcome differs between two runs": {
                "matrix": _fixture_case_matrix("SELF-3", flip_cmd, None, None),
                "golden": {"SELF-3": {"exit": 0, "expect_ok": None}},
                "should_fail": True,
            },
            "deterministic entry, but golden disagrees with real behaviour": {
                "matrix": _fixture_case_matrix(
                    "SELF-4",
                    "python3 -c \"print('actual')\"",
                    None,
                    "expected-substring",
                ),
                "golden": {"SELF-4": {"exit": 0, "expect_ok": True}},
                "should_fail": True,
            },
            "golden itself bakes in a failure": {
                "matrix": _fixture_case_matrix("SELF-5", "true", None, None),
                "golden": {"SELF-5": {"exit": 1, "expect_ok": None}},
                "should_fail": True,
            },
            "entry has no golden record at all": {
                "matrix": _fixture_case_matrix("SELF-6", "true", None, None),
                "golden": {},
                "should_fail": True,
            },
            "command exceeds its timeout": {
                "matrix": _fixture_case_matrix("SELF-7", "sleep 5", None, None),
                "golden": {"SELF-7": {"exit": 0, "expect_ok": None}},
                "should_fail": True,
                "timeout_s": 1,
            },
        }

        for name, case in cases.items():
            problems = check_fixtures(
                case["matrix"],
                case["golden"],
                root=root,
                timeout_s=case.get("timeout_s", 30),
            )
            failed = bool(problems)
            if failed != case["should_fail"]:
                failures.append(
                    f"{name}: expected should_fail={case['should_fail']}, got problems={problems!r}"
                )

    if not failures:
        print(
            f"acf_matrix_check self-test: {len(cases)} fixture-reproduction cases discriminate"
        )
    return failures


# --- Self-test: proves the schema and structural rules actually discriminate. ---


def _good_entry(entry_id: str, dimension: str, capability: str) -> dict[str, Any]:
    return {
        "id": entry_id,
        "dimension": dimension,
        "capability": capability,
        "statement": "What level 3 means for a user.",
        "level_target": 3,
        "test": {"cmd": "true", "fixture": None, "expect": None},
        "evidence_ref": "somewhere real",
    }


def _canonical_matrix() -> dict[str, Any]:
    """A matrix carrying exactly the 36 canonical capabilities -- the shape a real, honest matrix
    must have. Used as the self-test's positive baseline, so the self-test cannot be satisfied by a
    smaller placeholder matrix (that would defeat the point of proving the binding check works)."""
    entries = [
        _good_entry(f"{dimension[:3].upper()}-{i}", dimension, capability)
        for i, (dimension, capability) in enumerate(_CANONICAL_CAPABILITIES, start=1)
    ]
    # Re-key ids uniquely per dimension (COV-1..COV-5, RIG-1..RIG-6, ...).
    counters: dict[str, int] = {}
    for entry, (dimension, _capability) in zip(
        entries, _CANONICAL_CAPABILITIES, strict=True
    ):
        counters[dimension] = counters.get(dimension, 0) + 1
        entry["id"] = f"{dimension[:3].upper()}-{counters[dimension]}"
    return {"version": 1, "entries": entries}


def self_test() -> int:
    schema = load_schema()
    jsonschema.validators.validator_for(schema).check_schema(schema)

    cases: dict[str, dict[str, Any]] = {}

    good = _canonical_matrix()
    cases["good matrix, all 36 canonical capabilities present"] = {
        "data": good,
        "should_fail": False,
    }

    placeholder = {
        "version": 1,
        "entries": [
            _good_entry(f"{d[:3].upper()}-1", d, "Filler capability")
            for d in _SEVEN_DIMENSIONS
        ],
    }
    cases[
        "placeholder: one filler entry per dimension, not the 36 real capabilities"
    ] = {
        "data": placeholder,
        "should_fail": True,
    }

    missing_field = copy.deepcopy(good)
    del missing_field["entries"][0]["statement"]
    cases["entry missing required field 'statement'"] = {
        "data": missing_field,
        "should_fail": True,
    }

    bad_level = copy.deepcopy(good)
    bad_level["entries"][0]["level_target"] = 4
    cases["level_target out of range (4)"] = {"data": bad_level, "should_fail": True}

    non_int_level = copy.deepcopy(good)
    non_int_level["entries"][0]["level_target"] = "3"
    cases["level_target wrong type (string '3')"] = {
        "data": non_int_level,
        "should_fail": True,
    }

    unknown_dim = copy.deepcopy(good)
    unknown_dim["entries"][0]["dimension"] = "not-a-real-dimension"
    cases["unknown dimension"] = {"data": unknown_dim, "should_fail": True}

    dup_id = copy.deepcopy(good)
    dup_id["entries"].append(
        _good_entry(
            good["entries"][0]["id"], "rigor", "Duplicate id, distinct capability"
        )
    )
    cases["duplicate id"] = {"data": dup_id, "should_fail": True}

    missing_dim = copy.deepcopy(good)
    missing_dim["entries"] = [
        e for e in missing_dim["entries"] if e["dimension"] != "governance"
    ]
    cases["a dimension with zero entries (governance dropped)"] = {
        "data": missing_dim,
        "should_fail": True,
    }

    level3_no_evidence = copy.deepcopy(good)
    del level3_no_evidence["entries"][0]["evidence_ref"]
    cases["level_target 3 with evidence_ref omitted"] = {
        "data": level3_no_evidence,
        "should_fail": True,
    }

    level3_null_evidence = copy.deepcopy(good)
    level3_null_evidence["entries"][0]["evidence_ref"] = None
    cases["level_target 3 with evidence_ref explicitly null"] = {
        "data": level3_null_evidence,
        "should_fail": True,
    }

    test_missing_cmd = copy.deepcopy(good)
    test_missing_cmd["entries"][0]["test"] = {"fixture": None, "expect": None}
    cases["test object present but missing required 'cmd'"] = {
        "data": test_missing_cmd,
        "should_fail": True,
    }

    non_string_cmd = copy.deepcopy(good)
    non_string_cmd["entries"][0]["test"]["cmd"] = 123
    cases["test.cmd wrong type (integer)"] = {
        "data": non_string_cmd,
        "should_fail": True,
    }

    failures: list[str] = []
    for name, case in cases.items():
        problems = validate(case["data"], schema)
        failed = bool(problems)
        if failed != case["should_fail"]:
            failures.append(
                f"{name}: expected should_fail={case['should_fail']}, got problems={problems!r}"
            )

    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1

    print(f"acf_matrix_check self-test: {len(cases)} cases discriminate")

    # The real matrix on disk must itself be valid, cover exactly the 36 canonical capabilities, and
    # (best-effort, only when the private framework document is present) stay in sync with its wording.
    real = load_matrix()
    real_problems = validate(real, schema)
    if real_problems:
        for problem in real_problems:
            print(f"SELF-TEST FAIL: real matrix.yaml: {problem}", file=sys.stderr)
        return 1
    print(
        f"acf_matrix_check self-test: real matrix.yaml valid, {len(real['entries'])} entries"
    )

    doc_sync_problems = check_framework_doc_sync()
    if doc_sync_problems:
        for doc_problem in doc_sync_problems:
            print(f"SELF-TEST FAIL: framework doc sync: {doc_problem}", file=sys.stderr)
        return 1
    doc_path = _framework_doc_path()
    if doc_path is not None and doc_path.exists():
        print(
            "acf_matrix_check self-test: canonical list in sync with the framework document"
        )
    else:
        print(
            "acf_matrix_check self-test: framework document not present (public clone) -- doc-sync skipped"
        )

    fixture_failures = _self_test_fixtures()
    for failure in fixture_failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if fixture_failures:
        return 1

    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--check-fixtures",
        action="store_true",
        help=(
            "Run every matrix entry's test.cmd twice against the committed golden record "
            "(conformance/acf/fixtures-golden.json), proving it exists, reproduces deterministically, "
            "and matches. Not wired into public CI yet; run by hand or by a local gate."
        ),
    )
    parser.add_argument(
        "--record-golden",
        action="store_true",
        help=(
            "Maintenance-only: run every matrix entry's test.cmd once and overwrite "
            "conformance/acf/fixtures-golden.json. Never run by CI or --check-fixtures; the "
            "committed file is what --check-fixtures verifies against."
        ),
    )
    parsed = parser.parse_args(args)

    if parsed.self_test:
        return self_test()

    if parsed.record_golden:
        golden = record_golden(load_matrix(), root=_ROOT)
        _GOLDEN.write_text(
            json.dumps(golden, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"acf fixtures: recorded golden for {len(golden)} entries -> {_GOLDEN}")
        return 0

    if parsed.check_fixtures:
        problems = check_fixtures(load_matrix(), load_golden(), root=_ROOT)
        for problem in problems:
            print(f"VIOLATION [{problem.key}]: {problem.detail}", file=sys.stderr)
        if problems:
            return 1
        print(
            f"acf fixtures: ok, {len(load_golden())} entries reproduced deterministically"
        )
        return 0

    schema = load_schema()
    data = load_matrix()
    problems = validate(data, schema)
    for problem in problems:
        print(f"VIOLATION [{problem.key}]: {problem.detail}", file=sys.stderr)
    if problems:
        return 1
    print(
        f"acf matrix: ok, {len(data['entries'])} entries across {len(_SEVEN_DIMENSIONS)} dimensions"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
