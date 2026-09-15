"""The graph builder: typing with closure, relations, and the materialised glue edges (SPEC §6.3)."""

from __future__ import annotations

from typing import Any

from agentce.domain import DomainBinding
from agentce.graph import build_graph
from agentce.iri import event_iri, principal_iri

CREDIT = "agentce:CreditDecision"

DOMAIN = DomainBinding.from_dict(
    {
        "decision_types": [
            {
                "id": CREDIT,
                "subclass_of": "agentce:ConsequentialDecision",
                "consequential": True,
                "required_oversight_modality": "review_before",
            }
        ]
    }
)


def decision(
    event_id: str,
    time: str,
    *,
    modality: str = "review_before",
    acted_for: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "source": "urn:agentce:source:agent",
        "subject": "spiffe://corp/agents/a",
        "time": time,
        "type": "org.agent-conformance.evidence.Decision.v1",
        "agentcesourceclass": "self_report",
        "data": {
            "@type": "Decision",
            "decision_type": CREDIT,
            "oversight_modality": modality,
            "agent": {"id": "spiffe://corp/agents/a"},
            "acted_for": acted_for
            if acted_for is not None
            else ["spiffe://corp/humans/alice"],
        },
    }


def tool_call(event_id: str, time: str, decision_ref: str) -> dict[str, Any]:
    return {
        "id": event_id,
        "source": "urn:agentce:source:gw",
        "subject": "spiffe://corp/agents/a",
        "time": time,
        "type": "org.agent-conformance.evidence.ToolCall.v1",
        "agentcesourceclass": "enforcement_point",
        "data": {
            "@type": "ToolCall",
            "agent": {"id": "spiffe://corp/agents/a"},
            "acted_for": ["spiffe://corp/humans/alice"],
            "refs": {
                "decision": decision_ref,
                "instruction": "agentce:event/missing-instr",
            },
            "used": ["agentce:event/ctx-1"],
        },
    }


def test_types_with_subclass_closure(example_event: dict[str, Any]) -> None:
    store = build_graph([example_event])
    node = event_iri(str(example_event["id"]))
    assert store.is_a(node, "agentce:ToolCall")
    assert store.is_a(node, "agentce:Activity")


def test_relations_from_toolcall() -> None:
    call = tool_call("tc1", "2026-01-01T00:00:00Z", "agentce:event/d1")
    store = build_graph([call])
    node = event_iri("tc1")
    assert store.objects(node, "agentce:executes") == ["agentce:event/d1"]
    assert store.objects(node, "agentce:actsOn") == ["agentce:event/missing-instr"]
    assert store.objects(node, "prov:wasAssociatedWith") == ["spiffe://corp/agents/a"]
    assert store.objects(node, "prov:used") == ["agentce:event/ctx-1"]
    assert store.objects("spiffe://corp/agents/a", "prov:actedOnBehalfOf") == [
        principal_iri("spiffe://corp/humans/alice")
    ]


def test_dangling_ref_materialised() -> None:
    call = tool_call("tc1", "2026-01-01T00:00:00Z", "agentce:event/d1")
    store = build_graph([call])  # d1, missing-instr, ctx-1 are not ingested
    dangling = set(store.literal_values(event_iri("tc1"), "agentce:danglingRef"))
    assert "agentce:event/d1" in dangling
    assert "agentce:event/missing-instr" in dangling
    assert "agentce:event/ctx-1" in dangling


def test_no_dangling_when_target_present() -> None:
    dec = decision("d1", "2026-01-01T00:00:00Z")
    call = tool_call("tc1", "2026-01-01T00:01:00Z", "agentce:event/d1")
    store = build_graph([dec, call])
    assert "agentce:event/d1" not in store.literal_values(
        event_iri("tc1"), "agentce:danglingRef"
    )


def test_executes_consequential() -> None:
    dec = decision("d1", "2026-01-01T00:00:00Z")
    call = tool_call("tc1", "2026-01-01T00:01:00Z", "agentce:event/d1")
    store = build_graph([dec, call], domain=DOMAIN)
    assert store.literal_values(event_iri("tc1"), "agentce:executesConsequential") == [
        "true"
    ]
    # The consequential decision is typed by its domain subclass and reachable via closure.
    assert store.is_a(event_iri("d1"), "agentce:ConsequentialDecision")
    assert store.is_a(event_iri("d1"), "agentce:Decision")


def test_oversight_matches_declared() -> None:
    matching = build_graph(
        [decision("d1", "t", modality="review_before")], domain=DOMAIN
    )
    assert matching.literal_values(
        event_iri("d1"), "agentce:oversightModalityMatchesDeclared"
    ) == ["true"]
    mismatch = build_graph(
        [decision("d2", "t", modality="review_after")], domain=DOMAIN
    )
    assert mismatch.literal_values(
        event_iri("d2"), "agentce:oversightModalityMatchesDeclared"
    ) == ["false"]


def test_chain_terminus() -> None:
    store = build_graph(
        [decision("d1", "t", acted_for=["spiffe://svc", "spiffe://corp/humans/alice"])]
    )
    assert store.objects(event_iri("d1"), "agentce:chainTerminus") == [
        principal_iri("spiffe://corp/humans/alice")
    ]


def test_chain_verified() -> None:
    delegation = {
        "id": "dl1",
        "source": "urn:agentce:source:idp",
        "subject": "spiffe://corp/agents/a",
        "time": "2026-01-01T00:00:00Z",
        "type": "org.agent-conformance.evidence.DelegationIssued.v1",
        "agentcesourceclass": "enforcement_point",
        "data": {"@type": "DelegationIssued", "verification": {"status": "verified"}},
    }
    store = build_graph([delegation])
    assert store.literal_values(event_iri("dl1"), "agentce:chainVerified") == ["true"]


def test_delegation_chain_types_human_principal() -> None:
    # acted_for is a list of principal IRIs (no kind); the human overseer's kind is declared on the
    # DelegationIssued.chain, so the acted_for terminus resolves to a HumanPrincipal (SPEC §6.3).
    human = "urn:example:person:officer-1"
    delegation = {
        "id": "dl1",
        "source": "urn:agentce:source:idp",
        "subject": "spiffe://corp/agents/a",
        "time": "2026-01-01T00:00:00Z",
        "type": "org.agent-conformance.evidence.DelegationIssued.v1",
        "agentcesourceclass": "enforcement_point",
        "data": {
            "@type": "DelegationIssued",
            "verification": {"status": "verified"},
            "chain": [
                {"id": "spiffe://svc", "kind": "service"},
                {"id": human, "kind": "human"},
            ],
        },
    }
    tc = tool_call("tc1", "2026-01-01T00:01:00Z", "agentce:event/d1")
    tc["data"]["acted_for"] = ["spiffe://svc", human]
    store = build_graph([delegation, tc], domain=DOMAIN)
    assert store.is_a(principal_iri(human), "agentce:HumanPrincipal")
    assert store.objects(event_iri("tc1"), "agentce:chainTerminus") == [
        principal_iri(human)
    ]


def test_delegation_chain_ignores_non_list() -> None:
    delegation = {
        "id": "dl1",
        "source": "urn:agentce:source:idp",
        "subject": "spiffe://corp/agents/a",
        "time": "2026-01-01T00:00:00Z",
        "type": "org.agent-conformance.evidence.DelegationIssued.v1",
        "agentcesourceclass": "enforcement_point",
        "data": {"@type": "DelegationIssued", "chain": "not-a-list"},
    }
    store = build_graph([delegation])  # a malformed chain is ignored, not fatal
    assert store.literal_values(event_iri("dl1"), "agentce:chainVerified") == ["false"]


def test_preceded_by_reviewed_decision() -> None:
    earlier = decision("d1", "2026-01-01T00:00:00Z")
    approval = {
        "id": "ap1",
        "source": "urn:agentce:source:approvals",
        "subject": "spiffe://corp/agents/a",
        "time": "2026-01-01T00:00:30Z",
        "type": "org.agent-conformance.evidence.ApprovalDecided.v1",
        "agentcesourceclass": "independent_system",
        "data": {"@type": "ApprovalDecided", "refs": {"decision": "agentce:event/d1"}},
    }
    later = decision("d2", "2026-01-01T01:00:00Z")
    store = build_graph([earlier, approval, later], domain=DOMAIN)
    assert store.objects(event_iri("d2"), "agentce:precededBy") == [event_iri("d1")]


def test_build_is_deterministic() -> None:
    events = [
        decision("d1", "2026-01-01T00:00:00Z"),
        tool_call("tc1", "2026-01-01T00:01:00Z", "agentce:event/d1"),
    ]
    first = build_graph(events, domain=DOMAIN).triple_count()
    second = build_graph(events, domain=DOMAIN).triple_count()
    assert first == second
