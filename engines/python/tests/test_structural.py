"""The structural evaluator: PSP targets, paths, constraints, tolerance, and a SHACL cross-check."""

from __future__ import annotations

from agentce.psp import parse_shapes_ttl
from agentce.store import GraphStore
from agentce.structural import evaluate_control, resolve_path

SHAPE_TTL = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix agentce: <https://agent-conformance.org/vocab/evidence/v1#> .

agentce:OVS-03-Shape a sh:NodeShape ;
  sh:targetClass agentce:ToolCall ;
  agentce:targetWhere [ agentce:executesConsequential true ] ;
  sh:property [ sh:path agentce:chainTerminus ; sh:class agentce:HumanPrincipal ;
                sh:minCount 1 ; sh:name "E2a" ] ;
  sh:property [ sh:path ( agentce:executes agentce:oversightModalityMatchesDeclared ) ;
                sh:hasValue true ; sh:name "E3" ] .
"""


def _closure(store: GraphStore) -> None:
    store.add_subclass_closure(
        [
            ("agentce:ToolCall", "agentce:ToolCall"),
            ("agentce:ToolCall", "agentce:Activity"),
            ("agentce:HumanPrincipal", "agentce:HumanPrincipal"),
            ("agentce:HumanPrincipal", "agentce:Principal"),
        ]
    )


def _tool_call(
    store: GraphStore,
    node: str,
    *,
    consequential: bool,
    terminus_human: bool,
    matches: bool,
) -> None:
    store.add_type(node, "agentce:ToolCall")
    store.add_literal(
        node,
        "agentce:executesConsequential",
        "true" if consequential else "false",
        "xsd:boolean",
    )
    terminus = f"{node}-principal"
    store.add_edge(node, "agentce:chainTerminus", terminus)
    store.add_type(
        terminus,
        "agentce:HumanPrincipal" if terminus_human else "agentce:ServicePrincipal",
    )
    decision = f"{node}-decision"
    store.add_edge(node, "agentce:executes", decision)
    store.add_literal(
        decision,
        "agentce:oversightModalityMatchesDeclared",
        "true" if matches else "false",
        "xsd:boolean",
    )


def _shape():
    shapes = parse_shapes_ttl(SHAPE_TTL)
    return shapes, shapes[
        "https://agent-conformance.org/vocab/evidence/v1#OVS-03-Shape"
    ]


def test_conformant_when_all_expectations_met() -> None:
    store = GraphStore()
    _closure(store)
    _tool_call(
        store,
        "agentce:event/tc1",
        consequential=True,
        terminus_human=True,
        matches=True,
    )
    shapes, shape = _shape()
    outcome = evaluate_control(store, shape, shapes, control_id="OVS-03")
    assert outcome.outcome == "conformant"
    assert outcome.applicable == 1
    assert outcome.failed == 0


def test_non_conformant_on_unverified_oversight() -> None:
    store = GraphStore()
    _closure(store)
    _tool_call(
        store,
        "agentce:event/tc1",
        consequential=True,
        terminus_human=True,
        matches=False,
    )
    shapes, shape = _shape()
    outcome = evaluate_control(store, shape, shapes, control_id="OVS-03")
    assert outcome.outcome == "non-conformant"
    assert outcome.failed == 1
    assert any(v.constraint == "sh:hasValue" for v in outcome.violations)
    assert outcome.violations[0].message_key.startswith("OVS-03.")


def test_target_where_filters_population() -> None:
    store = GraphStore()
    _closure(store)
    # not consequential -> excluded from the applicable population by targetWhere
    _tool_call(
        store,
        "agentce:event/tc1",
        consequential=False,
        terminus_human=True,
        matches=False,
    )
    shapes, shape = _shape()
    outcome = evaluate_control(store, shape, shapes, control_id="OVS-03")
    assert outcome.applicable == 0
    assert outcome.outcome == "conformant"


def test_class_constraint_fails_for_service_principal() -> None:
    store = GraphStore()
    _closure(store)
    _tool_call(
        store,
        "agentce:event/tc1",
        consequential=True,
        terminus_human=False,
        matches=True,
    )
    shapes, shape = _shape()
    outcome = evaluate_control(store, shape, shapes, control_id="OVS-03")
    assert outcome.outcome == "non-conformant"
    assert any(v.constraint == "sh:class" for v in outcome.violations)


def test_ratio_tolerance_absorbs_one_failure() -> None:
    store = GraphStore()
    _closure(store)
    for i in range(10):
        _tool_call(
            store,
            f"agentce:event/tc{i}",
            consequential=True,
            terminus_human=True,
            matches=(i != 0),
        )
    shapes, shape = _shape()
    strict = evaluate_control(store, shape, shapes, control_id="OVS-03")
    assert strict.outcome == "non-conformant" and strict.failed == 1
    lenient = evaluate_control(
        store,
        shape,
        shapes,
        control_id="OVS-03",
        tolerance={"kind": "ratio", "max": "0.2"},
    )
    assert lenient.outcome == "conformant"


def test_resolve_sequence_path() -> None:
    store = GraphStore()
    store.add_edge("a", "agentce:executes", "d")
    store.add_literal(
        "d", "agentce:oversightModalityMatchesDeclared", "true", "xsd:boolean"
    )
    _shapes, shape = _shape()
    values = resolve_path(store, "a", shape.properties[1].path)
    assert [v.repr for v in values] == ["true"]
