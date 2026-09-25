"""Repository check: `conformance/acf/matrix.yaml` validates against its own schema, carries exactly the
36 named capabilities of the Agent Conformance Framework's seven dimensions (no more, no fewer, no wrong
name), and every entry id is unique. This proves the matrix's schema and structural validity, not (yet)
that every entry's `test.cmd` actually passes -- that live-execution proof is a separate, later mechanism
built on top of the same matrix once its corpus fixtures exist.

Every rejection carries a stable `key` (this script's own convention, mirroring
`tools/workflow_pins_check.py`'s discriminating self-test) so a caller can tell which structural rule
failed, not just that validation failed, and so a later caller can match on the key rather than parsing
prose.

The 36-capability canonical list below is hardcoded, not parsed from the framework document, because
that document lives outside this repository's tracked tree and this check must also run correctly from a
plain public clone (public CI, a verifier's clean clone, an end user's checkout) that never has it
present. When the document *is* present locally, `check_framework_doc_sync` additionally confirms the
canonical list has not drifted from its wording; that half is best-effort and silently skipped
otherwise, matching this codebase's existing pattern for optional local-only checks (see
`tools/governance_check.py`'s `PRIVATE_TERMS` handling).
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

import jsonschema
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_MATRIX = _ROOT / "conformance" / "acf" / "matrix.yaml"
_SCHEMA = _ROOT / "conformance" / "acf" / "matrix.schema.json"
_FRAMEWORK_DOC = _ROOT / "SPECS" / "AGENT-CONFORMANCE-FRAMEWORK.md"

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
    if not _FRAMEWORK_DOC.exists():
        return []
    text = _FRAMEWORK_DOC.read_text(encoding="utf-8")
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
    if _FRAMEWORK_DOC.exists():
        print(
            "acf_matrix_check self-test: canonical list in sync with the framework document"
        )
    else:
        print(
            "acf_matrix_check self-test: framework document not present (public clone) -- doc-sync skipped"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parsed = parser.parse_args(args)

    if parsed.self_test:
        return self_test()

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
