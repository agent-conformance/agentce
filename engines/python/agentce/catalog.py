"""Load and lint a control catalog (SPEC §7.1, §7.3-7.4).

A catalog is a directory with ``catalog.yaml`` (metadata and the control list), ``controls/*.yaml``
(one control each), and ``shapes/*.ttl`` (the Portable Shape Profile shapes the rung-2 controls
reference). ``lint_catalog`` validates every control against ``control.schema.json`` (which requires
each automated rule to carry a passed, a failed, and an inapplicable test case), parses each shape,
and re-runs every test case through the structural evaluator to confirm it produces the outcome it
claims -- so a broken control or fixture fails linting rather than shipping.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .domain import DomainBinding
from .graph import build_graph
from .psp import Shape, load_shapes
from .structural import evaluate_control

_EXPECTED_TO_CHECK = {"passed", "failed", "inapplicable"}


@dataclass
class ControlSpec:
    id: str
    version: str
    title: str
    applies_to_roles: list[str]
    mode: str
    rung: int
    severity: str
    min_source_class: str
    minimum_evidence: list[dict[str, str]]
    shape_path: str | None
    tolerance: dict[str, Any]
    test_cases: list[dict[str, str]]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Catalog:
    id: str
    version: str
    directory: Path
    controls: list[ControlSpec]
    shapes: dict[str, Shape]

    def shape_for(self, control: ControlSpec) -> Shape | None:
        if control.shape_path is None:
            return None
        want = f"{control.id}-Shape"
        for iri, shape in self.shapes.items():
            if iri.endswith(want):
                return shape
        for shape in self.shapes.values():
            if shape.target_class or shape.target_nodes or shape.target_where:
                return shape
        return None


def _control_from_dict(data: dict[str, Any]) -> ControlSpec:
    evaluation = data.get("evaluation", {})
    return ControlSpec(
        id=str(data.get("id", "")),
        version=str(data.get("version", "")),
        title=str(data.get("title", "")),
        applies_to_roles=list(
            data.get("applicability", {}).get("applies_to_roles", [])
        ),
        mode=str(evaluation.get("mode", "")),
        rung=int(evaluation.get("rung", 0)),
        severity=str(data.get("severity", "")),
        min_source_class=str(evaluation.get("min_source_class", "any")),
        minimum_evidence=list(evaluation.get("minimum_evidence", [])),
        shape_path=evaluation.get("shape"),
        tolerance=data.get("tolerance", {"kind": "count", "max": 0}),
        test_cases=list(data.get("test_cases", [])),
        raw=data,
    )


def load_catalog(directory: Path) -> Catalog:
    """Load ``catalog.yaml`` and every control and shape in ``directory``."""
    meta = (
        yaml.safe_load((directory / "catalog.yaml").read_text(encoding="utf-8")) or {}
    )
    controls: list[ControlSpec] = []
    shapes: dict[str, Shape] = {}
    for control_file in sorted((directory / "controls").glob("*.yaml")):
        data = yaml.safe_load(control_file.read_text(encoding="utf-8"))
        control = _control_from_dict(data)
        controls.append(control)
        if control.shape_path:
            shapes.update(load_shapes(directory / control.shape_path))
    return Catalog(
        id=str(meta.get("id", "")),
        version=str(meta.get("version", "")),
        directory=directory,
        controls=controls,
        shapes=shapes,
    )


def _load_control_schema() -> dict[str, Any]:
    text = (
        resources.files("agentce.data.schemas")
        .joinpath("control.schema.json")
        .read_text("utf-8")
    )
    parsed: dict[str, Any] = json.loads(text)
    return parsed


def _load_test_domain(directory: Path) -> DomainBinding:
    path = directory / "test" / "domain.yaml"
    return DomainBinding.load(path) if path.is_file() else DomainBinding.empty()


def _load_events(path: Path) -> list[dict[str, Any]]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


def _outcome_matches(expected: str, applicable: int, outcome: str) -> bool:
    if expected == "passed":
        return applicable > 0 and outcome == "conformant"
    if expected == "failed":
        return outcome == "non-conformant"
    if expected == "inapplicable":
        return applicable == 0
    return True  # insufficient_evidence / not_assessed are not re-derived structurally here


def lint_catalog(
    directory: Path, *, require_verification_flags: bool = False
) -> list[str]:
    """Return a list of problems; an empty list means the catalog is clean.

    With ``require_verification_flags`` (SPEC §7.3 B14), every crosswalk entry must carry a
    ``verified_against_text`` flag, so an unverified clause reference is never silently trusted;
    verification of the reference itself is a human action, but the flag must be present."""
    problems: list[str] = []
    if not (directory / "catalog.yaml").is_file():
        return [f"{directory}: no catalog.yaml"]
    schema = _load_control_schema()
    try:
        catalog = load_catalog(directory)
    except (yaml.YAMLError, OSError) as exc:
        return [f"{directory}: cannot load catalog ({exc})"]

    domain = _load_test_domain(directory)
    for control_file in sorted((directory / "controls").glob("*.yaml")):
        data = yaml.safe_load(control_file.read_text(encoding="utf-8"))
        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as exc:
            problems.append(f"{control_file.name}: schema: {exc.message}")
            continue
        control = _control_from_dict(data)
        if require_verification_flags:
            for entry in data.get("crosswalk", []):
                if "verified_against_text" not in entry:
                    problems.append(
                        f"{control.id}: crosswalk entry {entry.get('framework')}/"
                        f"{entry.get('clause')} lacks a verified_against_text flag"
                    )
        shape = catalog.shape_for(control)
        if control.rung == 2 and shape is None:
            problems.append(f"{control.id}: rung-2 control has no usable shape")
            continue
        for case in control.test_cases:
            problems.extend(_lint_case(catalog, control, shape, domain, case))
    return problems


def _lint_case(
    catalog: Catalog,
    control: ControlSpec,
    shape: Shape | None,
    domain: DomainBinding,
    case: dict[str, str],
) -> list[str]:
    fixture = catalog.directory / case["fixture"]
    if not fixture.is_file():
        return [f"{control.id}/{case['id']}: fixture {case['fixture']} not found"]
    if shape is None or case["expected"] not in _EXPECTED_TO_CHECK:
        return []
    events = _load_events(fixture)
    store = build_graph(events, domain=domain)
    outcome = evaluate_control(
        store, shape, catalog.shapes, control_id=control.id, tolerance=control.tolerance
    )
    if not _outcome_matches(case["expected"], outcome.applicable, outcome.outcome):
        return [
            f"{control.id}/{case['id']}: expected {case['expected']} but evaluated "
            f"{outcome.outcome} (applicable={outcome.applicable})"
        ]
    return []
