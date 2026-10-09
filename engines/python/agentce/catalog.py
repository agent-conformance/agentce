"""Load and lint a control catalog (SPEC §7.1, §7.3-7.4).

A catalog is a directory with ``catalog.yaml`` (metadata and the control list), ``controls/*.yaml``
(one control each), and ``shapes/*.ttl`` (the Portable Shape Profile shapes the rung-2 controls
reference). ``lint_catalog`` validates every control against ``control.schema.json`` (which requires
each automated rule to carry a passed, a failed, and an inapplicable test case), parses each shape,
and re-runs every test case through the structural evaluator to confirm it produces the outcome it
claims -- so a broken control or fixture fails linting rather than shipping. A catalog that sets
``min_crosswalk_frameworks: N`` (the baseline does, with 2) also fails linting for any control that
cites fewer than N distinct standards.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from fractions import Fraction
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from . import signing
from .domain import DomainBinding
from .error_catalogue import MESSAGE_KEYS
from .errors import InputError
from .graph import build_graph
from .i18n_format import format_message
from .psp import PropertyShape, Shape, load_shapes
from .structural import evaluate_control, evaluate_shape

_EXPECTED_TO_CHECK = {"passed", "failed", "inapplicable"}
_EVIDENCE_SHAPE_UNRESOLVED = "catalog.evidence_shape.unresolved"
_EVIDENCE_SHAPE_PASSED_CASE = "catalog.evidence_shape.passed_case_unjudged"

#: Files left out of the provenance digest: the detached signature and ``catalog.yaml`` itself (which
#: carries the provenance block), so the digest covers the catalog's rules and is non-circular.
_PROVENANCE_EXCLUDE = frozenset({signing.CATALOG_SIGNATURE_NAME, "catalog.yaml"})


def catalog_provenance_digest(directory: Path) -> str:
    """The content digest a catalog's provenance block must carry (SPEC §14.5 CP-3).

    Content-addresses every catalog file except the detached signature and ``catalog.yaml`` (which
    holds the provenance block), so a rebranded catalog whose rules or shapes differ fails validation.
    """
    return signing.digest_tree(directory, exclude=_PROVENANCE_EXCLUDE)


def _provenance_problems(directory: Path, meta: dict[str, Any]) -> list[str]:
    """Problems with the catalog's ``provenance`` block (SPEC §14.5 CP-3)."""
    provenance = meta.get("provenance")
    if not isinstance(provenance, dict):
        return ["catalog.yaml: missing provenance block (SPEC §14.5 CP-3)"]
    problems: list[str] = []
    source = provenance.get("source")
    if not isinstance(source, str) or not source.strip():
        problems.append("catalog.yaml: provenance.source is missing")
    if str(provenance.get("version")) != str(meta.get("version", "")):
        problems.append(
            "catalog.yaml: provenance.version does not match the catalog version"
        )
    expected = catalog_provenance_digest(directory)
    if provenance.get("digest") != expected:
        problems.append(
            f"catalog.yaml: provenance.digest does not match the catalog content (expected {expected})"
        )
    return problems


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
    evidence_shape_path: str | None = None


@dataclass
class Catalog:
    id: str
    version: str
    directory: Path
    controls: list[ControlSpec]
    shapes: dict[str, Shape]
    #: Each declared ``evaluation.evidence_shape`` path -> the IRI of the one node shape it holds.
    evidence_shape_iris: dict[str, str] = field(default_factory=dict)

    def shape_for(self, control: ControlSpec) -> Shape | None:
        if control.shape_path is None:
            return None
        want = f"{control.id}-Shape"
        for iri, shape in self.shapes.items():
            if iri.endswith(want):
                return shape
        for shape in self.shapes.values():
            if shape.target_classes or shape.target_nodes or shape.target_where:
                return shape
        return None

    def evidence_shape_for(self, control: ControlSpec) -> Shape | None:
        """The shape a focus must meet for the control to judge it (``evaluation.evidence_shape``);
        a focus that violates it lacks the evidence, so the outcome is insufficient_evidence."""
        if control.evidence_shape_path is None:
            return None
        return self.shapes[self.evidence_shape_iris[control.evidence_shape_path]]


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
        # A declared evidence shape is always resolved: a value that is not a path reads as "",
        # which load refuses, so it can never be skipped into a pass.
        evidence_shape_path=(
            (value if isinstance(value := evaluation["evidence_shape"], str) else "")
            if "evidence_shape" in evaluation
            else None
        ),
    )


def _load_evidence_shape(
    directory: Path, control_id: str, path: str
) -> tuple[str, dict[str, Shape]]:
    """The shapes in the file a control names as ``evaluation.evidence_shape``. The file must hold
    exactly one node shape with a target, whose IRI is returned with the file's shapes; anything else,
    including a file outside the catalog directory (which its signature would not cover), is
    refused, so a declared evidence shape can never be skipped and read as a pass."""
    resolved = (directory / path).resolve()
    inside = path != "" and resolved.is_relative_to(directory.resolve())
    loaded = load_shapes(resolved, path) if inside and resolved.is_file() else {}
    targeted = [
        iri
        for iri, shape in loaded.items()
        if shape.target_classes or shape.target_nodes or shape.target_where
    ]
    if len(targeted) != 1:
        entry = MESSAGE_KEYS[_EVIDENCE_SHAPE_UNRESOLVED]
        raise InputError(
            _EVIDENCE_SHAPE_UNRESOLVED,
            format_message(entry.cause, control=control_id, path=path),
            entry.fix,
        )
    return targeted[0], loaded


def load_catalog(directory: Path) -> Catalog:
    """Load ``catalog.yaml`` and every control and shape in ``directory``."""
    meta = (
        yaml.safe_load((directory / "catalog.yaml").read_text(encoding="utf-8")) or {}
    )
    controls: list[ControlSpec] = []
    shapes: dict[str, Shape] = {}
    evidence_shape_iris: dict[str, str] = {}
    for control_file in sorted((directory / "controls").glob("*.yaml")):
        data = yaml.safe_load(control_file.read_text(encoding="utf-8"))
        control = _control_from_dict(data)
        controls.append(control)
        if control.shape_path:
            shapes.update(
                load_shapes(directory / control.shape_path, control.shape_path)
            )
        if control.evidence_shape_path is not None:
            iri, loaded = _load_evidence_shape(
                directory, control.id, control.evidence_shape_path
            )
            evidence_shape_iris[control.evidence_shape_path] = iri
            shapes.update(loaded)
    return Catalog(
        id=str(meta.get("id", "")),
        version=str(meta.get("version", "")),
        directory=directory,
        controls=controls,
        shapes=shapes,
        evidence_shape_iris=evidence_shape_iris,
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


_SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}


def _tolerance_value(tolerance: dict[str, Any]) -> Fraction:
    """A comparable 'how much is tolerated' value: a count of N or a ratio in [0,1) as a Fraction."""
    try:
        return Fraction(str(tolerance.get("max", 0)))
    except (ValueError, ZeroDivisionError):
        return Fraction(0)


def _property_fingerprint(prop: PropertyShape) -> tuple[Any, ...]:
    return (
        repr(prop.path),
        prop.min_count,
        prop.max_count,
        prop.cls,
        prop.datatype,
        prop.node_kind,
        tuple(sorted(prop.in_values)) if prop.in_values else None,
        prop.has_value,
        prop.node,
        prop.qualified_value_shape,
        prop.qualified_min_count,
        prop.equals,
        prop.disjoint,
        prop.less_than,
        prop.less_than_or_equals,
        prop.min_inclusive,
        prop.max_inclusive,
    )


def _shape_fingerprint(shape: Shape) -> tuple[Any, ...]:
    """A shape's semantic content (SPEC §7.2's Portable Shape Profile), excluding its own IRI and any
    ``sh:name``/``sh:message`` annotation: two shapes with this same fingerprint test the same thing
    regardless of which control's file declares them."""
    return (
        tuple(shape.target_classes),
        tuple(sorted(shape.target_nodes)),
        tuple(sorted(shape.target_where)),
        tuple(sorted(_property_fingerprint(prop) for prop in shape.properties)),
    )


def _fixture_fingerprint(directory: Path, control: ControlSpec) -> tuple[Any, ...]:
    """A control's test fixtures, by content: the same ``case id``/``expected`` pairs over the exact
    same events mean two controls exercise the same scenarios, whatever their fixture file names."""
    cases: list[tuple[Any, ...]] = []
    for case in sorted(control.test_cases, key=lambda entry: entry["id"]):
        path = directory / case["fixture"]
        events: tuple[str, ...] | None = None
        if path.is_file():
            events = tuple(
                json.dumps(event, sort_keys=True) for event in _load_events(path)
            )
        cases.append((case["id"], case.get("expected"), events))
    return tuple(cases)


def _rule_uniqueness_pairs(catalog: Catalog) -> list[tuple[str, str]]:
    """Raw ``(later_control_id, earlier_control_id)`` pairs sharing an identical shape and fixture
    set, in control order. See ``rule_uniqueness_problems``, which formats these as messages, and
    ``new_rule_uniqueness_problems``, which excludes pairs already named in a committed baseline."""
    pairs: list[tuple[str, str]] = []
    seen: dict[tuple[Any, ...], str] = {}
    for control in catalog.controls:
        if control.rung != 2:
            continue
        shape = catalog.shape_for(control)
        if shape is None:
            continue
        key = (
            _shape_fingerprint(shape),
            _fixture_fingerprint(catalog.directory, control),
        )
        earlier = seen.get(key)
        if earlier is not None:
            pairs.append((control.id, earlier))
        else:
            seen[key] = control.id
    return pairs


def _rule_uniqueness_message(later: str, earlier: str) -> str:
    return (
        f"{later}: shares an identical shape and fixture set with {earlier}; "
        f"its rule does not test what its title says (SPEC §7.3 rule uniqueness)"
    )


def rule_uniqueness_problems(catalog: Catalog) -> list[str]:
    """SPEC §7.3: a control's rule must test what its own title says. Two rung-2 controls that share
    an identical shape (same target and every property constraint) and an identical fixture set (same
    case ids over the exact same events) mean one of them is silently testing the other's rule under
    its own title -- found in 18.37, where DOC-01 shipped with REC-01's shape and fixtures verbatim
    (in both the baseline and eu-ai-act catalogs), and ROB-02 shipped with DAT-01's shape and
    fixtures verbatim (in eu-ai-act). An empty list means every rung-2 control in the catalog tests
    something its own shape and fixtures do not share with any other control."""
    return [
        _rule_uniqueness_message(later, earlier)
        for later, earlier in _rule_uniqueness_pairs(catalog)
    ]


def _new_problems(
    pairs: list[tuple[str, str]], baseline_pairs: frozenset[frozenset[str]]
) -> list[str]:
    return [
        _rule_uniqueness_message(later, earlier)
        for later, earlier in pairs
        if frozenset((later, earlier)) not in baseline_pairs
    ]


def _stale_pairs(
    pairs: list[tuple[str, str]], baseline_pairs: frozenset[frozenset[str]]
) -> frozenset[frozenset[str]]:
    real_pairs = frozenset(frozenset(pair) for pair in pairs)
    return baseline_pairs - real_pairs


def new_rule_uniqueness_problems(
    catalog: Catalog, baseline_pairs: frozenset[frozenset[str]]
) -> list[str]:
    """Like ``rule_uniqueness_problems``, but a pair already named in ``baseline_pairs`` (disclosed,
    pre-existing debt -- see ``verification/gates/fixtures/rule_uniqueness/baseline.json`` and item
    18.37c, which pays it down) is not reported. A genuinely new duplicate -- any pair not already in
    the baseline -- is still reported, which is what makes the VG-CATALOG-RULE-UNIQUE gate a real
    regression check rather than a disabled one."""
    return _new_problems(_rule_uniqueness_pairs(catalog), baseline_pairs)


def stale_rule_uniqueness_pairs(
    catalog: Catalog, baseline_pairs: frozenset[frozenset[str]]
) -> frozenset[frozenset[str]]:
    """Baseline pairs (see ``new_rule_uniqueness_problems``) that are no longer real duplicates in
    this catalog. The baseline file exists to shrink as each pair is paid down (18.37a/18.37b/18.37c);
    an entry that stops being a real duplicate must be removed from the file, not left stale while
    pretending the debt is still live -- VG-CATALOG-RULE-UNIQUE fails on this, not just on a new
    pair. An empty result means every committed baseline pair is still a real duplicate."""
    return _stale_pairs(_rule_uniqueness_pairs(catalog), baseline_pairs)


def rule_uniqueness_report(
    catalog: Catalog, baseline_pairs: frozenset[frozenset[str]]
) -> tuple[frozenset[frozenset[str]], list[str]]:
    """``stale_rule_uniqueness_pairs`` and ``new_rule_uniqueness_problems`` together, computing
    ``_rule_uniqueness_pairs`` (which re-parses every rung-2 control's shape and fixtures) exactly
    once instead of twice -- the check driver needs both results for every catalog it scans."""
    pairs = _rule_uniqueness_pairs(catalog)
    return _stale_pairs(pairs, baseline_pairs), _new_problems(pairs, baseline_pairs)


def overlay_weakens_base(
    base_controls: list[ControlSpec], overlay_controls: list[ControlSpec]
) -> list[str]:
    """Return the ways an overlay weakens the base catalog (SPEC §7.3): an overlay may add controls
    or tighten thresholds, but a control that re-uses a base control's id must not lower its severity
    or loosen its tolerance. An empty list means the overlay only tightens or adds."""
    base_by_id = {control.id: control for control in base_controls}
    problems: list[str] = []
    for overlay in overlay_controls:
        base = base_by_id.get(overlay.id)
        if base is None:
            continue  # a new control the overlay adds, not a redefinition
        if _SEVERITY_RANK.get(overlay.severity, 0) < _SEVERITY_RANK.get(
            base.severity, 0
        ):
            problems.append(
                f"{overlay.id}: overlay lowers severity from {base.severity} to {overlay.severity}"
            )
        if _tolerance_value(overlay.tolerance) > _tolerance_value(base.tolerance):
            problems.append(
                f"{overlay.id}: overlay loosens tolerance beyond the base control"
            )
    return problems


def _outcome_matches(expected: str, applicable: int, outcome: str) -> bool:
    if expected == "passed":
        return applicable > 0 and outcome == "conformant"
    if expected == "failed":
        return outcome == "non-conformant"
    if expected == "inapplicable":
        return applicable == 0
    return True  # insufficient_evidence / not_assessed are not re-derived structurally here


def lint_catalog(
    directory: Path,
    *,
    require_verification_flags: bool = False,
    require_provenance: bool = False,
) -> list[str]:
    """Return a list of problems; an empty list means the catalog is clean.

    With ``require_verification_flags`` (SPEC §7.3 B14), every crosswalk entry must carry a
    ``verified_against_text`` flag, so an unverified clause reference is never silently trusted;
    verification of the reference itself is a human action, but the flag must be present. With
    ``require_provenance`` (SPEC §14.5 CP-3), ``catalog.yaml`` must carry a ``provenance`` block
    (``source``, ``version``, ``digest``) whose digest matches the catalog content, so a rebranded
    catalog either shows its origin or fails validation."""
    problems: list[str] = []
    if not (directory / "catalog.yaml").is_file():
        return [f"{directory}: no catalog.yaml"]
    schema = _load_control_schema()
    try:
        catalog = load_catalog(directory)
    except (yaml.YAMLError, OSError) as exc:
        return [f"{directory}: cannot load catalog ({exc})"]
    except InputError as exc:
        return [f"{exc.key}: {exc.cause}"]

    meta = (
        yaml.safe_load((directory / "catalog.yaml").read_text(encoding="utf-8")) or {}
    )
    if require_provenance:
        problems.extend(_provenance_problems(directory, meta))
    floor = meta.get("min_crosswalk_frameworks", 0)
    if not isinstance(floor, int) or isinstance(floor, bool) or floor < 0:
        problems.append(
            "catalog.yaml: min_crosswalk_frameworks must be a non-negative integer"
        )
        floor = 0

    domain = _load_test_domain(directory)
    for control_file in sorted((directory / "controls").glob("*.yaml")):
        data = yaml.safe_load(control_file.read_text(encoding="utf-8"))
        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as exc:
            problems.append(f"{control_file.name}: schema: {exc.message}")
            continue
        control = _control_from_dict(data)
        cited = {entry["framework"] for entry in data.get("crosswalk", [])}
        if len(cited) < floor:
            problems.append(
                f"catalog.crosswalk_floor: {control.id} cites {len(cited)} standard(s); "
                f"this catalog requires at least {floor}"
            )
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
    evidence_shape = catalog.evidence_shape_for(control)
    if evidence_shape is not None and case["expected"] == "passed":
        # The passed case must also be judged: its foci meet the evidence shape, and the evidence
        # shape targets at least one of them (a mistyped target would otherwise judge nothing).
        targeted, unjudged, _ = evaluate_shape(
            store, evidence_shape, catalog.shapes, control.id
        )
        if not targeted or unjudged:
            entry = MESSAGE_KEYS[_EVIDENCE_SHAPE_PASSED_CASE]
            return [
                f"{_EVIDENCE_SHAPE_PASSED_CASE}: "
                + format_message(entry.cause, control=control.id, case=case["id"])
            ]
    return []
