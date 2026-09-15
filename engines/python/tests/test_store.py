"""The SQLite graph store: triples, dedup, deterministic ordering, and class closure."""

from __future__ import annotations

from pathlib import Path

from agentce.store import RDF_TYPE, GraphStore


def test_edges_types_and_literals() -> None:
    store = GraphStore()
    store.add_type("a", "agentce:ToolCall")
    store.add_edge("a", "agentce:executes", "d")
    store.add_literal("a", "prov:atTime", "2026-01-01T00:00:00Z", "xsd:dateTime")
    assert store.objects("a", "agentce:executes") == ["d"]
    assert store.literal_values("a", "prov:atTime") == ["2026-01-01T00:00:00Z"]
    assert store.subjects(RDF_TYPE, "agentce:ToolCall") == ["a"]
    assert store.edge_count() == 2
    assert store.literal_count() == 1
    assert store.triple_count() == 3


def test_triples_are_a_set() -> None:
    store = GraphStore()
    store.add_edge("a", "p", "b")
    store.add_edge("a", "p", "b")
    assert store.edge_count() == 1


def test_ordering_is_deterministic() -> None:
    store = GraphStore()
    for obj in ("c", "a", "b"):
        store.add_edge("x", "p", obj)
    assert store.objects("x", "p") == ["a", "b", "c"]


def test_closure_membership() -> None:
    store = GraphStore()
    store.add_type("d", "agentce:ConsequentialDecision")
    store.add_subclass_closure(
        [
            ("agentce:ConsequentialDecision", "agentce:ConsequentialDecision"),
            ("agentce:ConsequentialDecision", "agentce:Decision"),
            ("agentce:ConsequentialDecision", "agentce:Activity"),
        ]
    )
    assert store.is_a("d", "agentce:Decision")
    assert store.is_a("d", "agentce:Activity")
    assert not store.is_a("d", "agentce:ToolCall")
    assert store.instances_of("agentce:Decision") == ["d"]


def test_persists_to_disk(tmp_path: Path) -> None:
    path = tmp_path / "graph.sqlite"
    store = GraphStore(path)
    store.add_edge("a", "p", "b")
    store.commit()
    store.close()
    reopened = GraphStore(path)
    assert reopened.objects("a", "p") == ["b"]
    reopened.close()
