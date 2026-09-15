"""Coverage of each PSP constraint and path kind in the structural evaluator."""

from __future__ import annotations

from agentce.psp import parse_shapes_ttl
from agentce.store import GraphStore
from agentce.structural import evaluate_shape, resolve_path

PREFIX = (
    "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
    "@prefix agentce: <https://agent-conformance.org/vocab/evidence/v1#> .\n"
    "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n"
)
THING = "agentce:Thing"
FOCUS = "agentce:event/x"
SHAPE_IRI = "https://agent-conformance.org/vocab/evidence/v1#S"


def _store() -> GraphStore:
    store = GraphStore()
    store.add_subclass_closure(
        [(THING, THING), ("agentce:HumanPrincipal", "agentce:HumanPrincipal")]
    )
    store.add_type(FOCUS, THING)
    return store


def _fails(body: str, store: GraphStore, extra: str = "") -> bool:
    ttl = (
        PREFIX
        + f"agentce:S a sh:NodeShape ; sh:targetClass {THING} ; {body} .\n{extra}"
    )
    shapes = parse_shapes_ttl(ttl)
    _applicable, failing, _violations = evaluate_shape(
        store, shapes[SHAPE_IRI], shapes, "C"
    )
    return FOCUS in failing


def test_max_count() -> None:
    body = "sh:property [ sh:path agentce:p ; sh:maxCount 1 ]"
    ok = _store()
    ok.add_edge(FOCUS, "agentce:p", "agentce:a")
    assert not _fails(body, ok)
    bad = _store()
    bad.add_edge(FOCUS, "agentce:p", "agentce:a")
    bad.add_edge(FOCUS, "agentce:p", "agentce:b")
    assert _fails(body, bad)


def test_datatype() -> None:
    body = "sh:property [ sh:path agentce:n ; sh:datatype xsd:integer ]"
    ok = _store()
    ok.add_literal(FOCUS, "agentce:n", "5", "xsd:integer")
    assert not _fails(body, ok)
    bad = _store()
    bad.add_literal(FOCUS, "agentce:n", "five", "xsd:string")
    assert _fails(body, bad)


def test_node_kind_iri() -> None:
    body = "sh:property [ sh:path agentce:k ; sh:nodeKind sh:IRI ]"
    ok = _store()
    ok.add_edge(FOCUS, "agentce:k", "agentce:a")
    assert not _fails(body, ok)
    bad = _store()
    bad.add_literal(FOCUS, "agentce:k", "lit", "xsd:string")
    assert _fails(body, bad)


def test_in() -> None:
    body = "sh:property [ sh:path agentce:p ; sh:in ( agentce:a agentce:b ) ]"
    ok = _store()
    ok.add_edge(FOCUS, "agentce:p", "agentce:a")
    assert not _fails(body, ok)
    bad = _store()
    bad.add_edge(FOCUS, "agentce:p", "agentce:c")
    assert _fails(body, bad)


def test_min_and_max_inclusive() -> None:
    body = "sh:property [ sh:path agentce:n ; sh:minInclusive 10 ; sh:maxInclusive 20 ]"
    ok = _store()
    ok.add_literal(FOCUS, "agentce:n", "15", "xsd:integer")
    assert not _fails(body, ok)
    low = _store()
    low.add_literal(FOCUS, "agentce:n", "9", "xsd:integer")
    assert _fails(body, low)
    high = _store()
    high.add_literal(FOCUS, "agentce:n", "21", "xsd:integer")
    assert _fails(body, high)


def test_equals_and_disjoint() -> None:
    equals_body = "sh:property [ sh:path agentce:p ; sh:equals agentce:q ]"
    ok = _store()
    ok.add_edge(FOCUS, "agentce:p", "agentce:a")
    ok.add_edge(FOCUS, "agentce:q", "agentce:a")
    assert not _fails(equals_body, ok)
    bad = _store()
    bad.add_edge(FOCUS, "agentce:p", "agentce:a")
    bad.add_edge(FOCUS, "agentce:q", "agentce:b")
    assert _fails(equals_body, bad)

    disjoint_body = "sh:property [ sh:path agentce:p ; sh:disjoint agentce:q ]"
    assert _fails(disjoint_body, ok)  # p and q share agentce:a
    assert not _fails(disjoint_body, bad)  # p and q are disjoint


def test_less_than() -> None:
    body = "sh:property [ sh:path agentce:a ; sh:lessThan agentce:b ]"
    ok = _store()
    ok.add_literal(FOCUS, "agentce:a", "1", "xsd:integer")
    ok.add_literal(FOCUS, "agentce:b", "2", "xsd:integer")
    assert not _fails(body, ok)
    bad = _store()
    bad.add_literal(FOCUS, "agentce:a", "5", "xsd:integer")
    bad.add_literal(FOCUS, "agentce:b", "2", "xsd:integer")
    assert _fails(body, bad)


def test_alternative_and_inverse_paths() -> None:
    alt = "sh:property [ sh:path [ sh:alternativePath ( agentce:a agentce:b ) ] ; sh:minCount 1 ]"
    store = _store()
    store.add_edge(FOCUS, "agentce:b", "agentce:x")
    assert not _fails(alt, store)

    inverse = "sh:property [ sh:path [ sh:inversePath agentce:p ] ; sh:minCount 1 ]"
    store2 = _store()
    store2.add_edge("agentce:other", "agentce:p", FOCUS)
    assert not _fails(inverse, store2)


def test_nested_node_shape() -> None:
    body = "sh:property [ sh:path agentce:child ; sh:node agentce:Sub ]"
    extra = (
        "agentce:Sub a sh:NodeShape ; "
        "sh:property [ sh:path agentce:flag ; sh:hasValue true ] .\n"
    )
    ok = _store()
    ok.add_edge(FOCUS, "agentce:child", "agentce:c1")
    ok.add_literal("agentce:c1", "agentce:flag", "true", "xsd:boolean")
    assert not _fails(body, ok, extra)
    bad = _store()
    bad.add_edge(FOCUS, "agentce:child", "agentce:c1")
    bad.add_literal("agentce:c1", "agentce:flag", "false", "xsd:boolean")
    assert _fails(body, bad, extra)


def test_qualified_min_count() -> None:
    body = (
        "sh:property [ sh:path agentce:child ; sh:qualifiedMinCount 1 ; "
        "sh:qualifiedValueShape agentce:Sub ]"
    )
    extra = (
        "agentce:Sub a sh:NodeShape ; "
        "sh:property [ sh:path agentce:flag ; sh:hasValue true ] .\n"
    )
    ok = _store()
    ok.add_edge(FOCUS, "agentce:child", "agentce:c1")
    ok.add_literal("agentce:c1", "agentce:flag", "true", "xsd:boolean")
    assert not _fails(body, ok, extra)
    bad = _store()
    bad.add_edge(FOCUS, "agentce:child", "agentce:c1")
    bad.add_literal("agentce:c1", "agentce:flag", "false", "xsd:boolean")
    assert _fails(body, bad, extra)


def test_inclusive_boundaries_are_conformant() -> None:
    # A value exactly equal to the bound satisfies min/maxInclusive (the comparison is <=, not <).
    body = "sh:property [ sh:path agentce:n ; sh:minInclusive 10 ; sh:maxInclusive 20 ]"
    at_min = _store()
    at_min.add_literal(FOCUS, "agentce:n", "10", "xsd:integer")
    assert not _fails(body, at_min)
    at_max = _store()
    at_max.add_literal(FOCUS, "agentce:n", "20", "xsd:integer")
    assert not _fails(body, at_max)


def test_datetime_inclusive_comparison() -> None:
    body = (
        "sh:property [ sh:path agentce:t ; "
        'sh:minInclusive "2026-01-01T00:00:00Z" ; sh:maxInclusive "2026-12-31T00:00:00Z" ]'
    )
    at_bound = _store()
    at_bound.add_literal(FOCUS, "agentce:t", "2026-01-01T00:00:00Z", "xsd:dateTime")
    assert not _fails(body, at_bound)  # equal to the lower bound is within
    before = _store()
    before.add_literal(FOCUS, "agentce:t", "2025-12-31T00:00:00Z", "xsd:dateTime")
    assert _fails(body, before)


def test_less_than_or_equals() -> None:
    body = "sh:property [ sh:path agentce:a ; sh:lessThanOrEquals agentce:b ]"
    equal = _store()
    equal.add_literal(FOCUS, "agentce:a", "2", "xsd:integer")
    equal.add_literal(FOCUS, "agentce:b", "2", "xsd:integer")
    assert not _fails(body, equal)  # equal satisfies lessThanOrEquals
    greater = _store()
    greater.add_literal(FOCUS, "agentce:a", "3", "xsd:integer")
    greater.add_literal(FOCUS, "agentce:b", "2", "xsd:integer")
    assert _fails(body, greater)


def test_resolve_alternative_returns_union() -> None:
    ttl = (
        PREFIX
        + "agentce:S a sh:NodeShape ; sh:targetClass agentce:Thing ; "
        + "sh:property [ sh:path [ sh:alternativePath ( agentce:a agentce:b ) ] ; sh:minCount 1 ] .\n"
    )
    shapes = parse_shapes_ttl(ttl)
    path = shapes[SHAPE_IRI].properties[0].path
    store = _store()
    store.add_edge(FOCUS, "agentce:a", "agentce:va")
    store.add_edge(FOCUS, "agentce:b", "agentce:vb")
    values = {v.repr for v in resolve_path(store, FOCUS, path)}
    assert values == {"agentce:va", "agentce:vb"}
