"""The graph builder: typing with closure, relations, and the materialised glue edges (SPEC §6.3)."""

from __future__ import annotations

from typing import Any

from agentce.domain import DomainBinding
from agentce.graph import build_graph
from agentce.iri import event_iri, principal_iri

CREDIT = "agentce:CreditDecision"
MINOR = "agentce:MinorDecision"

DOMAIN = DomainBinding.from_dict(
    {
        "decision_types": [
            {
                "id": CREDIT,
                "subclass_of": "agentce:ConsequentialDecision",
                "consequential": True,
                "required_oversight_modality": "review_before",
            },
            # Non-consequential: subclasses agentce:Decision directly, never ConsequentialDecision
            # (mirrors the shipped catalogs' own `dom:Minor`; see INC-01's `d3`/`c6` fail fixtures).
            {"id": MINOR, "subclass_of": "agentce:Decision", "consequential": False},
        ]
    }
)


def decision(
    event_id: str,
    time: str,
    *,
    modality: str = "review_before",
    acted_for: list[str] | None = None,
    decision_type: str = CREDIT,
    used: list[str] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "@type": "Decision",
        "decision_type": decision_type,
        "oversight_modality": modality,
        "agent": {"id": "spiffe://corp/agents/a"},
        "acted_for": acted_for
        if acted_for is not None
        else ["spiffe://corp/humans/alice"],
    }
    if used is not None:
        data["used"] = used
    return {
        "id": event_id,
        "source": "urn:agentce:source:agent",
        "subject": "spiffe://corp/agents/a",
        "time": time,
        "type": "org.agent-conformance.evidence.Decision.v1",
        "agentcesourceclass": "self_report",
        "data": data,
    }


def outcome(
    event_id: str, time: str, *, adverse: bool, decision_ref: str | None = None
) -> dict[str, Any]:
    data: dict[str, Any] = {"@type": "Outcome", "adverse": adverse}
    if decision_ref is not None:
        data["refs"] = {"decision": decision_ref}
    return {
        "id": event_id,
        "source": "urn:agentce:source:indep",
        "subject": "spiffe://corp/agents/a",
        "time": time,
        "type": "org.agent-conformance.evidence.Outcome.v1",
        "agentcesourceclass": "independent_system",
        "data": data,
    }


def notice(
    event_id: str, time: str, decision_ref: str, *, origin: str | None = None
) -> dict[str, Any]:
    refs: dict[str, Any] = {"decision": decision_ref}
    if origin is not None:
        refs["origin"] = origin
    return {
        "id": event_id,
        "source": "urn:agentce:source:indep",
        "subject": "spiffe://corp/agents/a",
        "time": time,
        "type": "org.agent-conformance.evidence.Notice.v1",
        "agentcesourceclass": "independent_system",
        "data": {"@type": "Notice", "refs": refs},
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


def test_adverse_outcome_linked_to_its_consequential_decision() -> None:
    dec = decision("d1", "t", decision_type=CREDIT)
    out = outcome("c1", "t", adverse=True, decision_ref="agentce:event/d1")
    store = build_graph([dec, out], domain=DOMAIN)
    assert store.literal_values(event_iri("c1"), "agentce:adverseOutcomeLinked") == [
        "true"
    ]


def test_non_adverse_outcome_is_vacuously_linked() -> None:
    out = outcome("c1", "t", adverse=False)
    store = build_graph([out], domain=DOMAIN)
    assert store.literal_values(event_iri("c1"), "agentce:adverseOutcomeLinked") == [
        "true"
    ]


def test_adverse_outcome_with_no_decision_ref_is_not_linked() -> None:
    out = outcome("c1", "t", adverse=True)
    store = build_graph([out], domain=DOMAIN)
    assert store.literal_values(event_iri("c1"), "agentce:adverseOutcomeLinked") == [
        "false"
    ]


def test_adverse_outcome_with_a_dangling_decision_ref_is_not_linked() -> None:
    """INC-01 (SPEC §7.4): a `refs.decision` that names no ingested event must not count as linked,
    even though the outcome also carries `agentce:danglingRef` for the same value -- the two literals
    are checked by different rules (TRN-03 fails on a dangling ref; INC-01 must fail here too)."""
    out = outcome("c1", "t", adverse=True, decision_ref="agentce:event/ghost")
    store = build_graph([out], domain=DOMAIN)
    assert store.literal_values(event_iri("c1"), "agentce:adverseOutcomeLinked") == [
        "false"
    ]
    assert "agentce:event/ghost" in store.literal_values(
        event_iri("c1"), "agentce:danglingRef"
    )


def test_adverse_outcome_linked_to_a_non_consequential_decision_is_not_linked() -> None:
    """INC-01 names 'their consequential decision' -- a real but non-consequential decision (the
    shipped catalogs' own `dom:Minor`) must not satisfy it, even though the ref resolves cleanly."""
    dec = decision("d1", "t", decision_type=MINOR)
    out = outcome("c1", "t", adverse=True, decision_ref="agentce:event/d1")
    store = build_graph([dec, out], domain=DOMAIN)
    assert store.is_a(event_iri("d1"), "agentce:ConsequentialDecision") is False
    assert store.literal_values(event_iri("c1"), "agentce:adverseOutcomeLinked") == [
        "false"
    ]


def test_explanation_reconstructable_when_notified_and_no_dangling_ref() -> None:
    dec = decision("d1", "t")
    note = notice("c1", "t", "agentce:event/d1")
    store = build_graph([dec, note], domain=DOMAIN)
    assert store.literal_values(
        event_iri("d1"), "agentce:explanationReconstructable"
    ) == ["true"]


def test_explanation_not_reconstructable_when_never_notified() -> None:
    dec = decision("d1", "t")
    store = build_graph([dec], domain=DOMAIN)
    assert store.literal_values(
        event_iri("d1"), "agentce:explanationReconstructable"
    ) == ["false"]


def test_explanation_not_reconstructable_when_notified_but_evidence_dangles() -> None:
    """TRN-03 (SPEC §7.6): the decision was notified, but its own evidence chain does not resolve --
    `used` names an event never ingested, so the decision carries `agentce:danglingRef` and the
    explanation that notice points at cannot actually be reconstructed."""
    dec = decision("d1", "t", used=["agentce:event/missing-input"])
    note = notice("c1", "t", "agentce:event/d1")
    store = build_graph([dec, note], domain=DOMAIN)
    assert "agentce:event/missing-input" in store.literal_values(
        event_iri("d1"), "agentce:danglingRef"
    )
    assert store.literal_values(
        event_iri("d1"), "agentce:explanationReconstructable"
    ) == ["false"]


def test_explanation_not_reconstructable_when_the_notice_itself_dangles() -> None:
    """TRN-03 (SPEC §7.6, round-2 critic finding 4): the decision's own evidence chain resolves
    cleanly, but the Notice that notified it carries a dangling `refs.*` value of its own (here
    `refs.origin`, a real `Refs` key per `spec/model/agentce-evidence.linkml.yaml` -- verifier round 1
    found the prior fixture used `refs.explanation_ref`, a key `Refs` does not define, so a real
    adapter could never produce it) -- `_dangling` lands that literal on the NOTICE node, not the
    Decision node, so `_explanation_reconstructable` must check the notice's dangling status too, not
    only the decision's. Before this fix, this case read `explanationReconstructable: true`."""
    dec = decision("d1", "t")
    note = notice("c1", "t", "agentce:event/d1", origin="agentce:event/missing-origin")
    store = build_graph([dec, note], domain=DOMAIN)
    assert "agentce:event/missing-origin" in store.literal_values(
        event_iri("c1"), "agentce:danglingRef"
    )
    assert store.literal_values(event_iri("d1"), "agentce:danglingRef") == []
    assert store.literal_values(
        event_iri("d1"), "agentce:explanationReconstructable"
    ) == ["false"]


def test_explanation_reconstructable_is_independent_of_notice_order() -> None:
    """Verifier-found regression: a decision notified by more than one Notice (one clean, one with
    its own dangling ref) used to read `explanationReconstructable` from whichever Notice was mapped
    LAST, so the same evidence gave a different verdict depending only on event order. Reconstructable
    means at least one notifying Notice is clean -- true in both orderings."""
    dec = decision("d1", "t")
    clean = notice("c1", "t", "agentce:event/d1")
    dangling = notice(
        "c2", "t", "agentce:event/d1", origin="agentce:event/missing-origin"
    )

    clean_last = build_graph([dec, dangling, clean], domain=DOMAIN)
    dangling_last = build_graph([dec, clean, dangling], domain=DOMAIN)

    assert clean_last.literal_values(
        event_iri("d1"), "agentce:explanationReconstructable"
    ) == ["true"]
    assert dangling_last.literal_values(
        event_iri("d1"), "agentce:explanationReconstructable"
    ) == ["true"]
