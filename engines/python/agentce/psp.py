"""Parse a Portable Shape Profile shape into a small AST (SPEC §7.2, ADR-0002).

The PSP is a strict, bounded subset of SHACL Core, so the AST is finite: a node shape has targets
(``sh:targetClass``, ``sh:targetNode``, and the engine-resolved ``agentce:targetWhere`` property-value
conjunction) and property shapes, each with a path (a predicate, an inverse, or a bounded sequence or
alternative) and the profile's constraints. The structural evaluator compiles this AST to queries over
the graph store; a SHACL library validates the same shape on small graphs as a cross-check.
``spec/rules/psp_check.py`` is the authoring-time checker for the profile; this module reads the same
term list (``psp-terms.json``) and refuses every shape that checker refuses, and every shape file that will
not parse, under a stable message key before a shape ever reaches the evaluator (18.34, 18.78).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from rdflib import RDF, BNode, Graph, Literal, URIRef
from rdflib.collection import Collection

from .error_catalogue import MESSAGE_KEYS
from .errors import InputError
from .i18n_format import format_message

SH = "http://www.w3.org/ns/shacl#"
AGENTCE = "https://agent-conformance.org/vocab/evidence/v1#"
PROV = "http://www.w3.org/ns/prov#"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
XSD = "http://www.w3.org/2001/XMLSchema#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"

_PREFIXES = [
    ("agentce:", AGENTCE),
    ("prov:", PROV),
    ("sh:", SH),
    ("rdfs:", RDFS),
    ("xsd:", XSD),
]


def curie(term: Any) -> str:
    """Compact an rdflib term to the store's representation (CURIE for known IRIs; lexical literal)."""
    if isinstance(term, Literal):
        return str(term)
    text = str(term)
    if text == RDF_NS + "type":
        return "rdf:type"
    for prefix, namespace in _PREFIXES:
        if text.startswith(namespace):
            return prefix + text[len(namespace) :]
    return text


# --- path AST ---


@dataclass(frozen=True)
class Predicate:
    iri: str


@dataclass(frozen=True)
class Inverse:
    path: "PathExpr"


@dataclass(frozen=True)
class Sequence:
    steps: tuple["PathExpr", ...]


@dataclass(frozen=True)
class Alternative:
    options: tuple["PathExpr", ...]


PathExpr = Predicate | Inverse | Sequence | Alternative


@dataclass
class PropertyShape:
    path: PathExpr
    name: str | None = None
    message_key: str | None = None
    min_count: int | None = None
    max_count: int | None = None
    cls: str | None = None
    datatype: str | None = None
    node_kind: str | None = None
    in_values: list[str] | None = None
    has_value: str | None = None
    node: str | None = None
    qualified_value_shape: str | None = None
    qualified_min_count: int | None = None
    equals: str | None = None
    disjoint: str | None = None
    less_than: str | None = None
    less_than_or_equals: str | None = None
    min_inclusive: str | None = None
    max_inclusive: str | None = None


@dataclass
class Shape:
    iri: str
    #: Every ``sh:targetClass``, sorted: SHACL reads more than one as a union.
    target_classes: list[str] = field(default_factory=list)
    target_nodes: list[str] = field(default_factory=list)
    target_where: list[tuple[str, str]] = field(default_factory=list)
    properties: list[PropertyShape] = field(default_factory=list)


def _sh(name: str) -> URIRef:
    return URIRef(SH + name)


def _parse_path(graph: Graph, node: Any) -> PathExpr:
    if isinstance(node, URIRef):
        return Predicate(curie(node))
    inverse = graph.value(node, _sh("inversePath"))
    if inverse is not None:
        return Inverse(_parse_path(graph, inverse))
    alternative = graph.value(node, _sh("alternativePath"))
    if alternative is not None:
        options = [_parse_path(graph, item) for item in Collection(graph, alternative)]
        return Alternative(tuple(options))
    if isinstance(node, BNode):  # an RDF list is a sequence path
        steps = [_parse_path(graph, item) for item in Collection(graph, node)]
        return Sequence(tuple(steps))
    return Predicate(curie(node))


def _value_list(graph: Graph, node: Any) -> list[str]:
    return [curie(item) for item in Collection(graph, node)]


def _int(term: Any) -> int | None:
    return int(term) if isinstance(term, Literal) else None


def _parse_property(graph: Graph, node: Any) -> PropertyShape:
    path = _parse_path(graph, graph.value(node, _sh("path")))
    prop = PropertyShape(path=path)
    name = graph.value(node, _sh("name"))
    prop.name = str(name) if name is not None else None
    message_key = graph.value(node, URIRef(AGENTCE + "messageKey"))
    prop.message_key = str(message_key) if message_key is not None else None
    prop.min_count = _int(graph.value(node, _sh("minCount")))
    prop.max_count = _int(graph.value(node, _sh("maxCount")))
    for attr, term_name in (
        ("cls", "class"),
        ("datatype", "datatype"),
        ("node_kind", "nodeKind"),
        ("node", "node"),
        ("equals", "equals"),
        ("disjoint", "disjoint"),
        ("less_than", "lessThan"),
        ("less_than_or_equals", "lessThanOrEquals"),
    ):
        value = graph.value(node, _sh(term_name))
        if value is not None:
            setattr(prop, attr, curie(value))
    for attr, term_name in (
        ("min_inclusive", "minInclusive"),
        ("max_inclusive", "maxInclusive"),
    ):
        value = graph.value(node, _sh(term_name))
        if value is not None:
            setattr(prop, attr, str(value))
    has_value = graph.value(node, _sh("hasValue"))
    if has_value is not None:
        prop.has_value = curie(has_value)
    in_list = graph.value(node, _sh("in"))
    if in_list is not None:
        prop.in_values = _value_list(graph, in_list)
    qualified = graph.value(node, _sh("qualifiedValueShape"))
    if qualified is not None:
        prop.qualified_value_shape = str(qualified)
        prop.qualified_min_count = _int(graph.value(node, _sh("qualifiedMinCount")))
    return prop


def parse_shapes(graph: Graph) -> dict[str, Shape]:
    """Parse every ``sh:NodeShape`` in ``graph`` into the PSP AST, keyed by shape IRI (as an IRI)."""
    shapes: dict[str, Shape] = {}
    for shape_node in graph.subjects(RDF.type, _sh("NodeShape")):
        shape = Shape(iri=str(shape_node))
        shape.target_classes = sorted(
            curie(c) for c in graph.objects(shape_node, _sh("targetClass"))
        )
        shape.target_nodes = [
            curie(n) for n in graph.objects(shape_node, _sh("targetNode"))
        ]
        for where in graph.objects(shape_node, URIRef(AGENTCE + "targetWhere")):
            for pred, obj in graph.predicate_objects(where):
                if pred != RDF.type:
                    shape.target_where.append((curie(pred), curie(obj)))
        for prop_node in graph.objects(shape_node, _sh("property")):
            shape.properties.append(_parse_property(graph, prop_node))
        shapes[str(shape_node)] = shape
    return shapes


#: The Portable Shape Profile's term lists (spec/rules/psp-terms.json, vendored byte-identical and held
#: in sync by tests/test_bundled_data.py): the same file spec/rules/psp_check.py reads, so the engine
#: refuses exactly the shapes the authoring-time checker refuses (18.78). Left unchecked, an excluded
#: predicate parses as ordinary Turtle and the reads above, which only ever ask for the predicates they
#: name, drop it silently: the shape would be evaluated as if the construct were not there.
_TERMS: dict[str, Any] = json.loads(
    resources.files("agentce.data").joinpath("psp-terms.json").read_text("utf-8")
)
_ALLOWED = frozenset(_TERMS["allowed"])
_PRIORITY_DENY: tuple[str, ...] = tuple(_TERMS["priority_deny"])
_MAX_PATH_LENGTH = int(_TERMS["max_path_length"])
_REGEX_META = frozenset(_TERMS["regex_meta"])
_RANGE_DATATYPES = frozenset(_TERMS["range_datatypes"])

#: The two features 18.34 gave their own keys; every other excluded feature is outside_profile.
_FEATURE_KEYS = {
    "sh:sparql": "catalog.shape.sparql_forbidden",
    "sh:js": "catalog.shape.script_forbidden",
    "sh:javascript": "catalog.shape.script_forbidden",
}
_OUTSIDE_PROFILE = "catalog.shape.outside_profile"
_PARSE_ERROR = "catalog.shape.parse_error"


def _path_feature(
    graph: Graph, node: Any, seen: frozenset[Any] = frozenset()
) -> str | None:
    """The excluded feature a property path uses, or None: a predicate, an inverse of a permitted
    path, or a sequence or alternative of at most ``max_path_length`` permitted paths."""
    if isinstance(node, URIRef):
        return None
    if node in seen:
        return "path (cyclic)"
    seen = seen | {node}
    for banned in ("zeroOrMorePath", "oneOrMorePath", "zeroOrOnePath"):
        if (node, _sh(banned), None) in graph:
            return f"sh:{banned}"
    inverse = graph.value(node, _sh("inversePath"))
    if inverse is not None:
        return _path_feature(graph, inverse, seen)
    alternative = graph.value(node, _sh("alternativePath"))
    if alternative is not None:
        if (alternative, RDF.first, None) not in graph:
            return "sh:alternativePath (not a list)"
        return _members_feature(
            graph, list(Collection(graph, alternative)), "alternative", seen
        )
    if (node, RDF.first, None) in graph:
        return _members_feature(graph, list(Collection(graph, node)), "sequence", seen)
    return "path (unsupported blank node)"


def _members_feature(
    graph: Graph, members: list[Any], kind: str, seen: frozenset[Any]
) -> str | None:
    if len(members) > _MAX_PATH_LENGTH:
        return f"path {kind} of {len(members)} (max {_MAX_PATH_LENGTH})"
    for member in members:
        feature = _path_feature(graph, member, seen)
        if feature is not None:
            return feature
    return None


def _structural_features(graph: Graph) -> list[list[str]]:
    """The path, pattern, range and targetWhere stages, in that order, each as the features it finds."""
    paths = [_path_feature(graph, node) for node in graph.objects(None, _sh("path"))]
    patterns = [
        "sh:pattern (non-literal regex)"
        for value in graph.objects(None, _sh("pattern"))
        if not str(value).startswith("^")
        or any(c in _REGEX_META for c in str(value)[1:])
    ]
    ranges = [
        f"{name} on a non-integer/dateTime bound"
        for name in ("minInclusive", "maxInclusive")
        for value in graph.objects(None, _sh(name))
        if not isinstance(value, Literal) or str(value.datatype) not in _RANGE_DATATYPES
    ]
    target_where = [
        "agentce:targetWhere (nested node, not a value equality)"
        for where in graph.objects(None, URIRef(AGENTCE + "targetWhere"))
        for _, value in graph.predicate_objects(where)
        if isinstance(value, BNode)
    ]
    return [[f for f in paths if f is not None], patterns, ranges, target_where]


def profile_feature(graph: Graph) -> str | None:
    """The first feature ``graph`` uses that the Portable Shape Profile excludes, or None.

    The order is fixed so the answer never depends on triple order or ``PYTHONHASHSEED``: the
    priority terms in list order, matched case-insensitively and named canonically (``sh:CLOSED`` is
    ``sh:closed``); then any other SHACL-namespace predicate the profile does not allow, as written,
    least by code point; then the path, pattern, range and targetWhere stages, each naming the
    code-point-least feature it finds (spec/rules/psp.md)."""
    used = sorted(
        {str(p)[len(SH) :] for p in graph.predicates() if str(p).startswith(SH)}
    )
    folded = {name.lower() for name in used}
    for term in _PRIORITY_DENY:
        if term.lower() in folded:
            return f"sh:{term}"
    for name in used:
        if name not in _ALLOWED:
            return f"sh:{name}"
    for found in _structural_features(graph):
        if found:
            return min(found)
    return None


def _bad_iri(graph: Graph) -> str | None:
    """rdflib keeps an IRI escape it cannot decode (``\\uZZZZ``) as a literal backslash instead of
    rejecting it, where TypeScript's and Java's parsers refuse it; a backslash never survives in a
    well-formed IRI, so one left over is a parse error here too."""
    for triple in graph:
        for term in triple:
            iris = [term] if isinstance(term, URIRef) else []
            if isinstance(term, Literal) and term.datatype is not None:
                iris.append(term.datatype)
            for iri in iris:
                if "\\" in str(iri):
                    return f"invalid escape in IRI <{iri}>"
    return None


def _parse_error(shown: str, detail: str) -> InputError:
    entry = MESSAGE_KEYS[_PARSE_ERROR]
    return InputError(
        _PARSE_ERROR, format_message(entry.cause, path=shown, detail=detail), entry.fix
    )


def parse_shapes_ttl(text: str, shown: str = "(inline)") -> dict[str, Shape]:
    """Parse PSP shapes from Turtle, refusing a file that will not parse or a shape outside the
    profile. ``shown`` names the shape file in the message (a catalog-relative path)."""
    graph = Graph()
    try:
        graph.parse(data=text, format="turtle")
    # rdflib raises BadSyntax, IndexError, AssertionError, ... on bad Turtle.
    except Exception as exc:
        raise _parse_error(
            shown, " ".join(str(exc).split())[:200] or type(exc).__name__
        ) from exc
    bad = _bad_iri(graph)
    if bad is not None:
        raise _parse_error(shown, bad)
    feature = profile_feature(graph)
    if feature is not None:
        key = _FEATURE_KEYS.get(feature, _OUTSIDE_PROFILE)
        entry = MESSAGE_KEYS[key]
        raise InputError(
            key, format_message(entry.cause, path=shown, feature=feature), entry.fix
        )
    return parse_shapes(graph)


def load_shapes(path: Path, shown: str) -> dict[str, Shape]:
    return parse_shapes_ttl(path.read_text(encoding="utf-8"), shown)
