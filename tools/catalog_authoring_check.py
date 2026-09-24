#!/usr/bin/env python3
"""catalog_authoring_check - the bring-your-own-catalog path: author, lint, and reject bad shapes.

CP-6 ("extension points, not forks") promises that a third party can add a control catalog without
touching engine code: author the catalog directory, run ``agentce catalog lint`` against it, sign it,
and load it with ``--catalog-dir``. The real engine lint (``agentce catalog lint``, wrapped here by
shelling out to the actual CLI so this tool never drifts from what a catalog author would run) validates
schema, evidence, and test-case outcomes -- but it does not reject a Portable Shape Profile shape that
uses a SPARQL-based constraint (``sh:sparql``), which the PSP definition (spec/rules/psp.md, item 0.5)
forbids by policy because it would let a shape carry arbitrary, non-portable query logic instead of the
small declarative vocabulary every engine implements identically. Left unchecked, such a shape parses as
ordinary Turtle and is silently accepted. This tool closes that gap: it scans every shape a catalog's
controls reference for forbidden SHACL constructs and rejects it with a specific, stable message key
*before* the shape ever reaches an engine, then runs the real ``agentce catalog lint`` for everything
else.

    catalog_authoring_check.py <catalog_dir>   validate a catalog directory (bring-your-own or base)
    catalog_authoring_check.py --self-test     prove it discriminates: a tiny synthetic catalog with a
                                                clean shape lints clean; the same catalog with an
                                                sh:sparql shape is rejected by name, not a generic
                                                parse failure

Uses only the standard library; shells out to the real CLI via ``uv run`` for the lint step. No network,
no learned component.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: SHACL predicates that embed arbitrary query or script logic, forbidden by the Portable Shape Profile
#: (spec/rules/psp.md) because they are not portable across the three engines' independent
#: implementations. Matched as a SHACL-namespaced token, prefixed (``sh:sparql``) or by full IRI.
_FORBIDDEN_SHAPE_PREDICATES = {
    "sparql": "catalog.shape.sparql_forbidden",
    "js": "catalog.shape.script_forbidden",
    "javascript": "catalog.shape.script_forbidden",
}
_PREDICATE_RE = re.compile(r"(?:\bsh:(\w+)\b|shacl#(\w+)\b)")


@dataclass
class Problem:
    key: str
    message: str

    def __str__(self) -> str:
        return f"{self.key}: {self.message}"


def _forbidden_predicates_in(shape_text: str) -> list[str]:
    found = []
    for match in _PREDICATE_RE.finditer(shape_text):
        name = (match.group(1) or match.group(2) or "").lower()
        if name in _FORBIDDEN_SHAPE_PREDICATES and name not in found:
            found.append(name)
    return found


def check_shapes(catalog_dir: Path) -> list[Problem]:
    """Scan every ``.ttl`` shape file under ``catalog_dir/shapes`` for a forbidden SHACL construct."""
    problems: list[Problem] = []
    shapes_dir = catalog_dir / "shapes"
    if not shapes_dir.is_dir():
        return problems
    for shape_file in sorted(shapes_dir.glob("*.ttl")):
        text = shape_file.read_text(encoding="utf-8")
        for name in _forbidden_predicates_in(text):
            key = _FORBIDDEN_SHAPE_PREDICATES[name]
            problems.append(
                Problem(
                    key,
                    f"{shape_file.relative_to(catalog_dir)}: uses sh:{name}, a SPARQL/script-based "
                    "SHACL construct the Portable Shape Profile forbids (spec/rules/psp.md) -- shapes "
                    "must use only the small declarative vocabulary every engine implements "
                    "identically.",
                )
            )
    return problems


def run_real_lint(catalog_dir: Path, repo_root: Path) -> Problem | None:
    """Shell out to the real ``agentce catalog lint``, so this tool never drifts from the CLI path a
    catalog author actually runs."""
    proc = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(repo_root / "engines" / "python"),
            "--frozen",
            "agentce",
            "catalog",
            "lint",
            str(catalog_dir),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        return Problem(
            "catalog.lint.failed",
            f"agentce catalog lint failed (exit {proc.returncode}):\n{proc.stdout}{proc.stderr}",
        )
    return None


def validate(catalog_dir: Path, repo_root: Path = ROOT) -> list[Problem]:
    """Every problem with ``catalog_dir`` as a bring-your-own catalog. The shape scan runs first and
    independently of the real lint, because the real lint's evaluator only rejects a rung-2 control
    with no usable shape at all -- it does not parse a present shape's constructs for policy
    conformance, so a forbidden construct would otherwise reach an engine silently accepted."""
    problems = check_shapes(catalog_dir)
    lint_problem = run_real_lint(catalog_dir, repo_root)
    if lint_problem is not None:
        problems.append(lint_problem)
    return problems


# --- self-test fixtures: a tiny synthetic third-party catalog, not eu-ai-act, not nist-ai-rmf ---

_GOOD_CONTROL = """\
id: TST-01
version: "2026.09"
title: Example third-party control
crosswalk:
  - {framework: eu-ai-act, clause: "Art. 99", relation: supports, verified_against_text: false}
applicability:
  applies_to_roles: [both]
evaluation:
  mode: manual
  rung: 0
  min_source_class: self_report
  minimum_evidence:
    - {event: Decision, class: any}
expectations:
  - {id: M1, text: "Example expectation for a bring-your-own catalog."}
severity: low
evidence_strength: partial
tolerance: {kind: count, max: 0}
test_cases:
  - {id: na, expected: not_assessed, fixture: test/TST-01/empty.jsonl}
"""

_BAD_CONTROL = _GOOD_CONTROL.replace(
    "  min_source_class: self_report\n",
    "  min_source_class: self_report\n  shape: shapes/TST-01.ttl\n",
)

_CATALOG_YAML = """\
id: sample-org
version: "2026.09"
title: "Sample third-party catalog (self-test fixture)"
controls:
  - controls/TST-01.yaml
"""

_GOOD_SHAPE = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix agentce: <https://agent-conformance.org/vocab/evidence/v1#> .

agentce:TST-01-Shape a sh:NodeShape ;
  sh:targetClass agentce:ConsequentialDecision ;
  sh:property [ sh:path prov:wasAssociatedWith ; sh:minCount 1 ; sh:name "M1" ] .
"""

_SPARQL_SHAPE = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix agentce: <https://agent-conformance.org/vocab/evidence/v1#> .

agentce:TST-01-Shape a sh:NodeShape ;
  sh:targetClass agentce:ConsequentialDecision ;
  sh:sparql [
    a sh:SPARQLConstraint ;
    sh:select "SELECT $this WHERE { $this a agentce:ConsequentialDecision }" ;
  ] .
"""


def _write_fixture(root: Path, control_yaml: str) -> Path:
    catalog_dir = root
    (catalog_dir / "controls").mkdir(parents=True, exist_ok=True)
    (catalog_dir / "test" / "TST-01").mkdir(parents=True, exist_ok=True)
    (catalog_dir / "catalog.yaml").write_text(_CATALOG_YAML, encoding="utf-8")
    (catalog_dir / "controls" / "TST-01.yaml").write_text(
        control_yaml, encoding="utf-8"
    )
    (catalog_dir / "test" / "TST-01" / "empty.jsonl").write_text("", encoding="utf-8")
    return catalog_dir


def self_test() -> int:
    ok = True
    with tempfile.TemporaryDirectory(
        prefix="agentce-catalog-authoring-selftest-"
    ) as tmp:
        tmp_path = Path(tmp)

        good_dir = _write_fixture(tmp_path / "good", _GOOD_CONTROL)
        (good_dir / "shapes").mkdir(exist_ok=True)
        (good_dir / "shapes" / "TST-01.ttl").write_text(_GOOD_SHAPE, encoding="utf-8")
        good_problems = validate(good_dir)
        good_ok = not good_problems
        print(f"self-test good-catalog: {'ok' if good_ok else 'FAIL'}")
        if not good_ok:
            ok = False
            for problem in good_problems:
                print(f"  {problem}", file=sys.stderr)

        bad_dir = _write_fixture(tmp_path / "bad-sparql", _BAD_CONTROL)
        (bad_dir / "shapes").mkdir(exist_ok=True)
        (bad_dir / "shapes" / "TST-01.ttl").write_text(_SPARQL_SHAPE, encoding="utf-8")
        bad_problems = validate(bad_dir)
        keys = [problem.key for problem in bad_problems]
        bad_ok = "catalog.shape.sparql_forbidden" in keys
        print(f"self-test bad-sparql-shape: {'ok' if bad_ok else 'FAIL'}")
        if not bad_ok:
            ok = False
            print(
                f"  wanted catalog.shape.sparql_forbidden; got keys {keys}",
                file=sys.stderr,
            )
        # Copy the fixture aside for inspection is unnecessary; tempdir is cleaned up on exit.
        shutil.rmtree(bad_dir, ignore_errors=True)

    print("SELF-TEST PASSED" if ok else "SELF-TEST FAILED")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) == 1:
        problems = validate(Path(argv[0]).resolve())
        for problem in problems:
            print(problem, file=sys.stderr)
        if problems:
            print(f"{len(problems)} problem(s)", file=sys.stderr)
            return 1
        print("CATALOG-AUTHORING OK")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
