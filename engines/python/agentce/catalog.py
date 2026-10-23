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

import errno
import json
import os
from dataclasses import dataclass, field
from fractions import Fraction
from importlib import resources
from pathlib import Path
from collections.abc import Iterable
from typing import Any

import jsonschema
import yaml

from . import signing
from .domain import DomainBinding
from .error_catalogue import MESSAGE_KEYS
from .errors import InputError, catalog_file_unloadable, catalog_unreadable
from .graph import build_graph
from .i18n_format import format_message
from .psp import PropertyShape, Shape, parse_shapes_ttl
from .safe_yaml import YamlProfileError, load_catalog_yaml
from .source_class import SOURCE_CLASS_FROM, SOURCE_CLASS_RANK
from .structural import evaluate_control, evaluate_shape

_EXPECTED_TO_CHECK = {"passed", "failed", "inapplicable"}
_EVIDENCE_SHAPE_UNRESOLVED = "catalog.evidence_shape.unresolved"
_SHAPE_UNRESOLVED = "catalog.shape.unresolved"
_EVIDENCE_SHAPE_PASSED_CASE = "catalog.evidence_shape.passed_case_unjudged"
_MIN_SOURCE_CLASS_UNKNOWN = "catalog.control.min_source_class_unknown"
_SOURCE_CLASS_FROM_UNKNOWN = "catalog.control.source_class_from_unknown"
_REQUIRES_DENOMINATOR_INVALID = "catalog.control.requires_denominator_invalid"

#: The coverage-denominator kinds a profile may declare (SPEC §6.5).
DENOMINATOR_KINDS = frozenset(
    {
        "provider_usage_export",
        "egress_proxy_log",
        "firewall_log",
        "system_of_record_export",
        "admission_attestation",
    }
)

#: Files left out of the provenance digest: the detached signature and ``catalog.yaml`` itself (which
#: carries the provenance block), so the digest covers the catalog's rules and is non-circular.
_PROVENANCE_EXCLUDE = frozenset({signing.CATALOG_SIGNATURE_NAME, "catalog.yaml"})


def catalog_provenance_digest(source: Path | signing.CatalogFiles) -> str:
    """The content digest a catalog's provenance block must carry (SPEC §14.5 CP-3).

    Content-addresses every catalog file except the detached signature and ``catalog.yaml`` (which
    holds the provenance block), so a rebranded catalog whose rules or shapes differ fails validation.
    ``source`` is the folder, or the files a load read from it (18.171), whose bytes are then the ones
    digested.
    """
    if isinstance(source, signing.CatalogFiles):
        return source.digest(exclude=_PROVENANCE_EXCLUDE)
    return signing.digest_tree(source, exclude=_PROVENANCE_EXCLUDE)


def _provenance_problems(
    files: signing.CatalogFiles, meta: dict[str, Any]
) -> list[str]:
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
    expected = catalog_provenance_digest(files)
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
    #: ``evaluation.source_class_from``: the graph links to the records the judged class also rests on.
    source_class_from: list[str] = field(default_factory=list)
    #: ``evaluation.requires_denominator``: the declared denominator a verdict needs (18.37e).
    requires_denominator: RequiredDenominator | None = None


@dataclass(frozen=True)
class RequiredDenominator:
    """A control that judges only where the subject declares an independent coverage denominator of one of
    ``kinds`` whose count of ``covers`` records reconciles with what was captured (SPEC §6.5, §7.3)."""

    kinds: frozenset[str]
    covers: str


@dataclass
class Catalog:
    id: str
    version: str
    directory: Path
    controls: list[ControlSpec]
    shapes: dict[str, Shape]
    #: Each declared ``evaluation.evidence_shape`` path -> the IRI of the one node shape it holds.
    evidence_shape_iris: dict[str, str] = field(default_factory=dict)
    #: The bytes the catalog was loaded from (18.171): its signature check, provenance digest and
    #: packaged copy read these, never the folder again. None only for a catalog built in memory.
    files: signing.CatalogFiles | None = None

    def shape_for(self, control: ControlSpec) -> Shape | None:
        if control.shape_path is None:
            return None
        want = f"{control.id}-Shape"
        for iri, shape in self.shapes.items():
            if iri.endswith(want):
                return shape
        for shape in self.shapes.values():
            if shape.has_target:
                return shape
        return None

    def evidence_shape_for(self, control: ControlSpec) -> Shape | None:
        """The shape a focus must meet for the control to judge it (``evaluation.evidence_shape``);
        a focus that violates it lacks the evidence, so the outcome is insufficient_evidence."""
        if control.evidence_shape_path is None:
            return None
        return self.shapes[self.evidence_shape_iris[control.evidence_shape_path]]


def _min_source_class(control_id: str, evaluation: Any) -> str:
    """The control's ``evaluation.min_source_class``, exactly one of the SPEC §6.4 classes. Anything else
    (missing, null, empty, not a string, ``any``, another case or another word) is refused when the catalog
    loads, never read as no minimum."""
    present = isinstance(evaluation, dict) and "min_source_class" in evaluation
    value = evaluation["min_source_class"] if present else None
    if isinstance(value, str) and value in SOURCE_CLASS_RANK:
        return value
    entry = MESSAGE_KEYS[_MIN_SOURCE_CLASS_UNKNOWN]
    shown = json.dumps(value, ensure_ascii=False) if present else "(missing)"
    raise InputError(
        _MIN_SOURCE_CLASS_UNKNOWN,
        format_message(entry.cause, control=control_id, value=shown),
        entry.fix,
    )


def _source_class_from(control_id: str, evaluation: dict[str, Any]) -> list[str]:
    """The named ``source_class_from`` links, distinct and sorted (each is ASCII, so code-point order is byte
    order). A value that is not a list of known link names is refused when the catalog loads, never dropped: a
    dropped link would judge the control on the focus record alone."""
    if "source_class_from" not in evaluation:
        return []
    value = evaluation["source_class_from"]
    if isinstance(value, list) and all(
        isinstance(name, str) and name in SOURCE_CLASS_FROM for name in value
    ):
        return sorted(set(value))
    entry = MESSAGE_KEYS[_SOURCE_CLASS_FROM_UNKNOWN]
    raise InputError(
        _SOURCE_CLASS_FROM_UNKNOWN,
        format_message(
            entry.cause,
            control=control_id,
            value=json.dumps(value, ensure_ascii=False),
        ),
        entry.fix,
    )


def _control_from_dict(data: dict[str, Any]) -> ControlSpec:
    evaluation = data.get("evaluation", {})
    control_id = str(data.get("id", ""))
    min_source_class = _min_source_class(control_id, evaluation)
    # A non-string shape is kept as "" (not a covered file), which load refuses; null stays "no shape".
    shape = evaluation.get("shape")
    return ControlSpec(
        id=control_id,
        version=str(data.get("version", "")),
        title=str(data.get("title", "")),
        applies_to_roles=list(
            data.get("applicability", {}).get("applies_to_roles", [])
        ),
        mode=str(evaluation.get("mode", "")),
        rung=int(evaluation.get("rung", 0)),
        severity=str(data.get("severity", "")),
        min_source_class=min_source_class,
        minimum_evidence=list(evaluation.get("minimum_evidence", [])),
        shape_path=shape if shape is None or isinstance(shape, str) else "",
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
        source_class_from=_source_class_from(control_id, evaluation),
        requires_denominator=_requires_denominator(control_id, evaluation),
    )


def _requires_denominator(
    control_id: str, evaluation: dict[str, Any]
) -> RequiredDenominator | None:
    """The declared ``requires_denominator``. Anything but a non-empty list of known denominator kinds and an event
    type name is refused when the catalog loads, never dropped: a dropped requirement would judge without it."""
    if "requires_denominator" not in evaluation:
        return None
    value = evaluation["requires_denominator"]
    if isinstance(value, dict) and set(value) == {"kinds", "covers"}:
        kinds, covers = value["kinds"], value["covers"]
        if (
            isinstance(kinds, list)
            and kinds
            and all(isinstance(k, str) and k in DENOMINATOR_KINDS for k in kinds)
            and len(set(kinds)) == len(kinds)
            and isinstance(covers, str)
            and covers
        ):
            return RequiredDenominator(frozenset(kinds), covers)
    entry = MESSAGE_KEYS[_REQUIRES_DENOMINATOR_INVALID]
    raise InputError(
        _REQUIRES_DENOMINATOR_INVALID,
        format_message(
            entry.cause,
            control=control_id,
            value=json.dumps(value, ensure_ascii=False, sort_keys=True),
        ),
        entry.fix,
    )


def _refuse_uncovered(key: str, control_id: str, path: str) -> InputError:
    entry = MESSAGE_KEYS[key]
    return InputError(
        key, format_message(entry.cause, control=control_id, path=path), entry.fix
    )


def link_outside(rel: str) -> InputError:
    """catalog.link_outside for ``rel``, a covered file that links outside the catalog folder (18.181)."""
    entry = MESSAGE_KEYS["catalog.link_outside"]
    return InputError(
        "catalog.link_outside", format_message(entry.cause, path=rel), entry.fix
    )


def _load_evidence_shape(
    files: signing.CatalogFiles, control_id: str, path: str
) -> tuple[str, dict[str, Shape]]:
    """The shapes in the file a control names as ``evaluation.evidence_shape``. The file must be one the
    catalog digest covers, named exactly (18.168), and hold exactly one node shape with a target, whose IRI
    is returned with the file's shapes; anything else, including a file outside the catalog directory
    (which its signature would not cover), is refused, so a declared evidence shape can never be skipped
    and read as a pass."""
    inside = path in files.files and signing.inside_folder(
        files.root, files.root / path
    )
    loaded = parse_shapes_ttl(_text(files, path), path) if inside else {}
    targeted = [iri for iri, shape in loaded.items() if shape.has_target]
    if len(targeted) != 1:
        raise _refuse_uncovered(_EVIDENCE_SHAPE_UNRESOLVED, control_id, path)
    return targeted[0], loaded


def _control_files(covered: Iterable[str]) -> list[str]:
    """The covered ``controls/<name>.yaml`` files, one level deep as the listing always was, sorted."""
    return sorted(
        rel
        for rel in covered
        if rel.startswith("controls/") and rel.count("/") == 1 and rel.endswith(".yaml")
    )


def load_catalog(directory: Path) -> Catalog:
    """Load ``catalog.yaml`` and every control and shape in ``directory``. Only files the catalog digest
    covers are read, each by its exact name (`signing.covered_files`, 18.168): a dot-named control file
    is never loaded, and a shape path that names anything else is refused, so a signed catalog cannot
    change what it loads without changing its digest. The load walks the folder as the digest does, so
    a folder it cannot read is refused as input.catalog_unreadable, as verify and sign refuse it.
    Every covered file is read once (`signing.read_catalog_files`, 18.171) and kept as
    ``Catalog.files``, which the signature check and the provenance digest read in turn."""
    try:
        return _load_catalog(directory)
    except (OSError, UnicodeError) as exc:
        raise catalog_unreadable(directory, exc) from exc


def _text(files: signing.CatalogFiles, rel: str) -> str:
    """A catalog file's covered bytes as text; bytes that are not UTF-8 are refused naming the file (18.184i)."""
    try:
        return files.text(rel)
    except UnicodeDecodeError as exc:
        raise catalog_file_unloadable(files.root, rel, "is not valid UTF-8") from exc


def _yaml_mapping(files: signing.CatalogFiles, rel: str) -> dict[str, Any]:
    """``catalog.yaml`` or a control file as one YAML mapping, read as `load_untrusted_yaml` reads it, under the
    SPEC 8.7 YAML profile (18.184i, 18.184u): a key written twice, YAML the engine refuses to load, or anything but one mapping (an
    empty file included) is refused naming the file, never read with its last value, as nothing, or skipped."""
    text = _text(files, rel)
    try:
        data = load_catalog_yaml(text)
    except YamlProfileError as exc:
        raise catalog_file_unloadable(
            files.root, rel, exc.violation.message(), f"{exc.violation.hint}."
        ) from exc
    except yaml.YAMLError as exc:
        raise catalog_file_unloadable(
            files.root, rel, f"is not YAML the engine can load: {exc}"
        ) from exc
    except RecursionError as exc:
        raise catalog_file_unloadable(
            files.root, rel, "is nested too deeply to read safely"
        ) from exc
    if not isinstance(data, dict):
        raise catalog_file_unloadable(files.root, rel, "does not hold one YAML mapping")
    return data


def _load_catalog(directory: Path) -> Catalog:
    # catalog.yaml first, as before: a folder without one is refused naming it, and one that is there
    # but is not a regular file (a FIFO would block the read) is refused unopened (18.181).
    meta_path = directory / "catalog.yaml"
    if not signing.is_regular_file(meta_path) and os.path.lexists(meta_path):
        raise OSError(errno.EINVAL, "not a regular file", str(meta_path))
    files = signing.read_catalog_files(
        directory, first={"catalog.yaml": meta_path.read_bytes()}
    )
    meta = _yaml_mapping(files, "catalog.yaml")
    controls: list[ControlSpec] = []
    shapes: dict[str, Shape] = {}
    evidence_shape_iris: dict[str, str] = {}
    for rel in _control_files(files.files):
        control = _control_from_dict(_yaml_mapping(files, rel))
        controls.append(control)
        if control.shape_path is not None:
            if control.shape_path not in files.files:
                raise _refuse_uncovered(
                    _SHAPE_UNRESOLVED, control.id, control.shape_path
                )
            shapes.update(
                parse_shapes_ttl(_text(files, control.shape_path), control.shape_path)
            )
        if control.evidence_shape_path is not None:
            iri, loaded = _load_evidence_shape(
                files, control.id, control.evidence_shape_path
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
        files=files,
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
    return (
        DomainBinding.load(path)
        if signing.is_regular_file(path)
        else DomainBinding.empty()
    )


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
        if signing.is_regular_file(path):
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
        # A declared denominator is part of the rule: the same shape judged only where a declared count
        # reconciles tests something else (18.37e, DOC-03 beside OVS-06).
        key = (
            _shape_fingerprint(shape),
            _fixture_fingerprint(catalog.directory, control),
            control.requires_denominator,
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
    return load_and_lint(
        directory,
        require_verification_flags=require_verification_flags,
        require_provenance=require_provenance,
    )[1]


def load_and_lint(
    directory: Path,
    *,
    require_verification_flags: bool = False,
    require_provenance: bool = False,
) -> tuple[Catalog | None, list[str]]:
    """`lint_catalog`, also returning the catalog it loaded (None when it could not load one), so a
    caller that acts on a clean lint (``catalog sign``) uses the bytes that were linted (18.171)."""
    if not os.path.lexists(directory / "catalog.yaml"):
        # One that is there but is not a file is refused by the load as unreadable, never read as absent.
        return None, [f"{directory}: no catalog.yaml"]
    try:
        catalog = load_catalog(directory)
    except OSError as exc:
        return None, [f"{directory}: cannot load catalog ({exc})"]
    except InputError as exc:
        return None, [f"{exc.key}: {exc.cause}"]
    return catalog, _lint_loaded(
        catalog,
        require_verification_flags=require_verification_flags,
        require_provenance=require_provenance,
    )


def _lint_loaded(
    catalog: Catalog, *, require_verification_flags: bool, require_provenance: bool
) -> list[str]:
    problems: list[str] = []
    directory = catalog.directory
    schema = _load_control_schema()

    # The bytes the load read (18.171): what is linted is what the provenance digest covers.
    files = catalog.files
    assert files is not None
    meta = _yaml_mapping(files, "catalog.yaml")
    if require_provenance:
        # A catalog published with provenance is the folder handed over: a file linked from outside
        # it, or reached through a linked folder, would not travel with it (18.181, inbox row 397).
        problems.extend(
            f"catalog.link_outside: {link_outside(rel).cause}"
            for rel in sorted(files.outside)
        )
        problems.extend(_provenance_problems(files, meta))
    floor = meta.get("min_crosswalk_frameworks", 0)
    if not isinstance(floor, int) or isinstance(floor, bool) or floor < 0:
        problems.append(
            "catalog.yaml: min_crosswalk_frameworks must be a non-negative integer"
        )
        floor = 0

    domain = _load_test_domain(directory)
    for rel in _control_files(files.files):
        data = _yaml_mapping(files, rel)
        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as exc:
            problems.append(f"{rel.removeprefix('controls/')}: schema: {exc.message}")
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
    try:
        if not signing.is_regular_file(fixture):
            return [f"{control.id}/{case['id']}: fixture {case['fixture']} not found"]
        if shape is None or case["expected"] not in _EXPECTED_TO_CHECK:
            return []
        events = _load_events(fixture)
    except OSError as exc:  # unreadable, never "not found", on every Python (18.169)
        return [
            f"{control.id}/{case['id']}: fixture {case['fixture']} could not be read ({exc.strerror})"
        ]
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
