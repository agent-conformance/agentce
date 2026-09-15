"""Cross-check the compiled structural evaluator against a SHACL library on a small graph (ADR-0002).

pyshacl validates the same shape over an rdflib rendering of the store; the compiled evaluator must
agree on which focus nodes fail. The shape uses only profile-core constructs (targetClass, a predicate
path, sh:class over a directly-typed node, sh:minCount) where the two are expected to agree exactly.
"""

from __future__ import annotations

import pyshacl
from rdflib import RDF, Graph, Literal, Namespace, URIRef

from agentce.psp import curie, parse_shapes_ttl
from agentce.store import GraphStore
from agentce.structural import evaluate_shape

SH = Namespace("http://www.w3.org/ns/shacl#")
AGENTCE = "https://agent-conformance.org/vocab/evidence/v1#"
PROV = "http://www.w3.org/ns/prov#"
XSD = "http://www.w3.org/2001/XMLSchema#"

SHAPE_TTL = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix agentce: <https://agent-conformance.org/vocab/evidence/v1#> .

agentce:S a sh:NodeShape ;
  sh:targetClass agentce:ToolCall ;
  sh:property [ sh:path agentce:chainTerminus ; sh:class agentce:HumanPrincipal ; sh:minCount 1 ] .
"""


def _expand(term: str) -> URIRef:
    if term == "rdf:type":
        return URIRef(str(RDF.type))
    for prefix, namespace in (
        ("agentce:", AGENTCE),
        ("prov:", PROV),
        ("sh:", str(SH)),
        ("xsd:", XSD),
    ):
        if term.startswith(prefix):
            return URIRef(namespace + term[len(prefix) :])
    return URIRef(term)


def _store_to_graph(store: GraphStore) -> Graph:
    graph = Graph()
    for s, p, o in store.conn.execute("SELECT s, p, o FROM edges").fetchall():
        graph.add((_expand(s), _expand(p), _expand(o)))
    for s, p, val, datatype in store.conn.execute(
        "SELECT s, p, val, datatype FROM literals"
    ).fetchall():
        graph.add((_expand(s), _expand(p), Literal(val, datatype=_expand(datatype))))
    return graph


def _shacl_failing(store: GraphStore) -> tuple[set[str], bool]:
    shape_graph = Graph()
    shape_graph.parse(data=SHAPE_TTL, format="turtle")
    conforms, results, _text = pyshacl.validate(
        _store_to_graph(store),
        shacl_graph=shape_graph,
        inference="none",
        advanced=False,
    )
    failing: set[str] = set()
    for result in results.subjects(RDF.type, SH.ValidationResult):
        focus = results.value(result, SH.focusNode)
        if focus is not None:
            failing.add(curie(focus))
    return failing, conforms


def _build_store() -> GraphStore:
    store = GraphStore()
    store.add_subclass_closure(
        [
            ("agentce:ToolCall", "agentce:ToolCall"),
            ("agentce:HumanPrincipal", "agentce:HumanPrincipal"),
        ]
    )
    # tc1 conforms: a human chain terminus.
    store.add_type("agentce:event/tc1", "agentce:ToolCall")
    store.add_edge("agentce:event/tc1", "agentce:chainTerminus", "agentce:principal/p1")
    store.add_type("agentce:principal/p1", "agentce:HumanPrincipal")
    # tc2 fails: no chain terminus at all.
    store.add_type("agentce:event/tc2", "agentce:ToolCall")
    store.commit()
    return store


def test_compiled_evaluator_agrees_with_shacl() -> None:
    store = _build_store()
    shapes = parse_shapes_ttl(SHAPE_TTL)
    shape = shapes[AGENTCE + "S"]
    _applicable, failing, _violations = evaluate_shape(store, shape, shapes, "S")

    shacl_failing, conforms = _shacl_failing(store)
    assert failing == {"agentce:event/tc2"}
    assert failing == shacl_failing
    assert conforms is False
