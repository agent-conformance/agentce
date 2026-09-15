"""The structural evaluator: compile a PSP shape to queries over the graph store (SPEC §7.2, §9.2).

Target nodes are the applicable population; each is checked against the shape's property shapes, and a
node with any failing constraint is a failure. Outcomes are computed at the population level against
the control's declared ``tolerance`` (§9.2): ``conformant`` when failures are within tolerance,
``non-conformant`` otherwise. Class membership uses the materialised ``rdfs:subClassOf*`` closure and
no other inference. Each violation is ``{focus, path, constraint, message_key}``; a SHACL library
cross-checks the same shape on small graphs (ADR-0002).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import Any

from .psp import (
    Alternative,
    Inverse,
    PathExpr,
    Predicate,
    PropertyShape,
    Sequence,
    Shape,
)
from .store import GraphStore


@dataclass(frozen=True)
class Value:
    repr: str
    is_iri: bool
    datatype: str | None = None


@dataclass(frozen=True)
class Violation:
    focus: str
    path: str
    constraint: str
    message_key: str

    def to_json(self) -> dict[str, str]:
        return {
            "focus": self.focus,
            "path": self.path,
            "constraint": self.constraint,
            "message_key": self.message_key,
        }


def render_path(path: PathExpr) -> str:
    if isinstance(path, Predicate):
        return path.iri
    if isinstance(path, Inverse):
        return f"^{render_path(path.path)}"
    if isinstance(path, Sequence):
        return "(" + " ".join(render_path(step) for step in path.steps) + ")"
    return "(" + " | ".join(render_path(option) for option in path.options) + ")"


def _predicate_values(store: GraphStore, focus: str, predicate: str) -> list[Value]:
    values = [Value(obj, True) for obj in store.objects(focus, predicate)]
    values += [
        Value(val, False, datatype)
        for val, datatype in store.literals(focus, predicate)
    ]
    return values


def resolve_path(store: GraphStore, focus: str, path: PathExpr) -> list[Value]:
    if isinstance(path, Predicate):
        return _predicate_values(store, focus, path.iri)
    if isinstance(path, Inverse):
        if isinstance(path.path, Predicate):
            return [Value(s, True) for s in store.subjects(path.path.iri, focus)]
        return []  # deeper inverses are outside the profile
    if isinstance(path, Alternative):
        out: list[Value] = []
        for option in path.options:
            out.extend(resolve_path(store, focus, option))
        return out
    # Sequence: traverse IRIs step by step; the last step yields the values.
    current = [focus]
    for index, step in enumerate(path.steps):
        collected: list[Value] = []
        for node in current:
            collected.extend(resolve_path(store, node, step))
        if index == len(path.steps) - 1:
            return collected
        current = [value.repr for value in collected if value.is_iri]
    return []


def _as_number(value: str) -> Fraction | None:
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError):
        return None


def _as_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _le(a: str, b: str) -> bool | None:
    na, nb = _as_number(a), _as_number(b)
    if na is not None and nb is not None:
        return na <= nb
    da, db = _as_datetime(a), _as_datetime(b)
    if da is not None and db is not None:
        return da <= db
    return None


def _check_property(
    store: GraphStore,
    focus: str,
    prop: PropertyShape,
    shapes: dict[str, Shape],
    control_id: str,
) -> list[Violation]:
    values = resolve_path(store, focus, prop.path)
    path_text = render_path(prop.path)
    failed: list[str] = []  # constraint ids that failed

    if prop.min_count is not None and len(values) < prop.min_count:
        failed.append("sh:minCount")
    if prop.max_count is not None and len(values) > prop.max_count:
        failed.append("sh:maxCount")
    if prop.has_value is not None and not any(v.repr == prop.has_value for v in values):
        failed.append("sh:hasValue")
    if prop.cls is not None and not all(
        v.is_iri and store.is_a(v.repr, prop.cls) for v in values
    ):
        failed.append("sh:class")
    if prop.datatype is not None and not all(
        (not v.is_iri and v.datatype == prop.datatype) for v in values
    ):
        failed.append("sh:datatype")
    if prop.node_kind is not None:
        want_iri = prop.node_kind == "sh:IRI"
        if not all(v.is_iri == want_iri for v in values):
            failed.append("sh:nodeKind")
    if prop.in_values is not None and not all(
        v.repr in set(prop.in_values) for v in values
    ):
        failed.append("sh:in")
    if prop.min_inclusive is not None and not all(
        _le(prop.min_inclusive, v.repr) for v in values
    ):
        failed.append("sh:minInclusive")
    if prop.max_inclusive is not None and not all(
        _le(v.repr, prop.max_inclusive) for v in values
    ):
        failed.append("sh:maxInclusive")
    if prop.equals is not None:
        others = {v.repr for v in _predicate_values(store, focus, prop.equals)}
        if {v.repr for v in values} != others:
            failed.append("sh:equals")
    if prop.disjoint is not None:
        others = {v.repr for v in _predicate_values(store, focus, prop.disjoint)}
        if {v.repr for v in values} & others:
            failed.append("sh:disjoint")
    for attr, constraint, ok in (
        ("less_than", "sh:lessThan", lambda a, b: _le(a, b) is True and a != b),
        ("less_than_or_equals", "sh:lessThanOrEquals", lambda a, b: _le(a, b) is True),
    ):
        other_path = getattr(prop, attr)
        if other_path is not None:
            other_values = _predicate_values(store, focus, other_path)
            if not all(ok(v.repr, o.repr) for v in values for o in other_values):
                failed.append(constraint)
    if prop.node is not None:
        nested = shapes.get(prop.node) or _shape_by_curie(shapes, prop.node)
        if nested is not None and not all(
            v.is_iri and not _node_violations(store, v.repr, nested, shapes, control_id)
            for v in values
        ):
            failed.append("sh:node")
    if prop.qualified_value_shape is not None and prop.qualified_min_count is not None:
        nested = shapes.get(prop.qualified_value_shape) or _shape_by_curie(
            shapes, prop.qualified_value_shape
        )
        conforming = sum(
            1
            for v in values
            if v.is_iri
            and nested is not None
            and not _node_violations(store, v.repr, nested, shapes, control_id)
        )
        if conforming < prop.qualified_min_count:
            failed.append("sh:qualifiedMinCount")

    key = prop.message_key or (
        f"{control_id}.{prop.name}" if prop.name else f"{control_id}.{path_text}"
    )
    return [Violation(focus, path_text, constraint, key) for constraint in failed]


def _shape_by_curie(shapes: dict[str, Shape], ref: str) -> Shape | None:
    for shape in shapes.values():
        if shape.iri.endswith(ref.split(":", 1)[-1]):
            return shape
    return None


def _node_violations(
    store: GraphStore,
    focus: str,
    shape: Shape,
    shapes: dict[str, Shape],
    control_id: str,
) -> list[Violation]:
    out: list[Violation] = []
    for prop in shape.properties:
        out.extend(_check_property(store, focus, prop, shapes, control_id))
    return out


def _target_nodes(store: GraphStore, shape: Shape) -> list[str]:
    focus: set[str] = set(shape.target_nodes)
    if shape.target_class is not None:
        focus.update(store.instances_of(shape.target_class))
    if not shape.target_where:
        return sorted(focus)
    kept = []
    for node in sorted(focus):
        if all(_has_value(store, node, pred, val) for pred, val in shape.target_where):
            kept.append(node)
    return kept


def _has_value(store: GraphStore, node: str, predicate: str, value: str) -> bool:
    if value in store.objects(node, predicate):
        return True
    return value in store.literal_values(node, predicate)


@dataclass
class ControlOutcome:
    control: str
    outcome: str
    applicable: int
    failed: int
    violations: list[Violation]

    def to_json(self) -> dict[str, Any]:
        return {
            "control": self.control,
            "outcome": self.outcome,
            "population": {"applicable": self.applicable, "failed": self.failed},
            "violations": [v.to_json() for v in self.violations],
        }


def evaluate_shape(
    store: GraphStore, shape: Shape, shapes: dict[str, Shape], control_id: str
) -> tuple[list[str], set[str], list[Violation]]:
    """Return (applicable focus nodes, failing focus nodes, violations) for ``shape``."""
    applicable = _target_nodes(store, shape)
    failing: set[str] = set()
    violations: list[Violation] = []
    for focus in applicable:
        node_violations: list[Violation] = []
        for prop in shape.properties:
            node_violations.extend(
                _check_property(store, focus, prop, shapes, control_id)
            )
        if node_violations:
            failing.add(focus)
            violations.extend(node_violations)
    return applicable, failing, violations


def _within_tolerance(applicable: int, failed: int, tolerance: dict[str, Any]) -> bool:
    kind = tolerance.get("kind", "count")
    if kind == "ratio" and applicable > 0:
        return Fraction(failed, applicable) <= Fraction(str(tolerance.get("max", "0")))
    return failed <= int(tolerance.get("max", 0))


def evaluate_control(
    store: GraphStore,
    shape: Shape,
    shapes: dict[str, Shape],
    *,
    control_id: str,
    tolerance: dict[str, Any] | None = None,
) -> ControlOutcome:
    """Evaluate one control's shape and apply its tolerance to reach a structural outcome."""
    applicable, failing, violations = evaluate_shape(store, shape, shapes, control_id)
    tolerance = tolerance or {"kind": "count", "max": 0}
    conformant = _within_tolerance(len(applicable), len(failing), tolerance)
    outcome = "conformant" if conformant else "non-conformant"
    return ControlOutcome(
        control_id,
        outcome,
        len(applicable),
        len(failing),
        sorted(violations, key=lambda v: (v.focus, v.constraint)),
    )
