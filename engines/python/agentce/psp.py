"""Parse a Portable Shape Profile shape into a small AST (SPEC §7.2, ADR-0002).

The PSP is a strict, bounded subset of SHACL Core (``psp_check`` gates every shape before it reaches
here), so the AST is finite: a node shape has targets (``sh:targetClass``, ``sh:targetNode``, and the
engine-resolved ``agentce:targetWhere`` property-value conjunction) and property shapes, each with a
path (a predicate, an inverse, or a bounded sequence or alternative) and the profile's constraints.
The structural evaluator compiles this AST to queries over the graph store; a SHACL library validates
the same shape on small graphs as a cross-check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rdflib import RDF, BNode, Graph, Literal, URIRef
from rdflib.collection import Collection

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
    target_class: str | None = None
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
        target_class = graph.value(shape_node, _sh("targetClass"))
        if target_class is not None:
            shape.target_class = curie(target_class)
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


def parse_shapes_ttl(text: str) -> dict[str, Shape]:
    graph = Graph()
    graph.parse(data=text, format="turtle")
    return parse_shapes(graph)


def load_shapes(path: Path) -> dict[str, Shape]:
    return parse_shapes_ttl(path.read_text(encoding="utf-8"))
