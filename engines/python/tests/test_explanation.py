"""The explanation renderer: edge citations, mandatory slots, and not-reconstructable (SPEC §8.1)."""

from __future__ import annotations

from typing import Any

from agentce import explanation
from agentce.domain import DomainBinding
from agentce.graph import build_graph
from agentce.iri import event_iri

DOMAIN = DomainBinding.from_dict(
    {"decision_types": [{"id": "credit:LoanApproval", "consequential": True}]}
)


def decision(eid: str, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "@type": "Decision",
        "decision_type": "credit:LoanApproval",
        "ai_role": "decides",
        "options": [
            {"id": "approve", "label": "Approve"},
            {"id": "deny", "label": "Deny"},
        ],
        "chosen": "approve",
        "agent": {"id": "agent:triage"},
        "used": ["ci:application", "ci:credit-score"],
        "acted_for": [{"id": "human:officer", "kind": "human"}],
    }
    data.update(overrides)
    return {"id": eid, "time": "2026-06-01T00:00:00.000Z", "data": data}


def _narrative(event: dict[str, Any]) -> explanation.Narrative:
    store = build_graph([event], domain=DOMAIN)
    try:
        return explanation.render_decision(event, store)
    finally:
        store.close()


def test_fully_reconstructable_decision() -> None:
    narrative = _narrative(decision("d1"))
    assert narrative.reconstructable is True
    slots = {s.slot: s for s in narrative.sentences}
    assert set(slots) == set(explanation.MANDATORY_SLOTS)
    # every sentence cites at least one edge
    assert all(s.citations for s in narrative.sentences)
    assert "not reconstructable" not in narrative.render()


def test_role_cites_the_agent_edge() -> None:
    narrative = _narrative(decision("d1"))
    role = next(s for s in narrative.sentences if s.slot == "role")
    assert role.citations == ["agent:triage"]
    assert "'decides'" in role.text


def test_inputs_cite_every_context_item() -> None:
    narrative = _narrative(decision("d1"))
    inputs = next(s for s in narrative.sentences if s.slot == "inputs")
    assert inputs.citations == ["ci:application", "ci:credit-score"]


def test_chosen_cites_the_option_iri() -> None:
    node = event_iri("d1")
    narrative = _narrative(decision("d1"))
    chosen = next(s for s in narrative.sentences if s.slot == "chosen")
    assert chosen.citations == [f"{node}/option/approve"]
    assert "Approve" in chosen.text


def test_human_slot_cites_a_human_principal() -> None:
    narrative = _narrative(decision("d1"))
    human = next(s for s in narrative.sentences if s.slot == "human")
    assert human.reconstructable is True
    assert human.citations and human.citations[0].startswith("agentce:principal/")


def test_missing_chosen_is_not_reconstructable() -> None:
    narrative = _narrative(decision("d2", chosen=None))
    chosen = next(s for s in narrative.sentences if s.slot == "chosen")
    assert chosen.reconstructable is False
    assert explanation.NOT_RECONSTRUCTABLE in chosen.text
    assert narrative.reconstructable is False
    # the fallback sentence still carries a citation to the decision it examined
    assert chosen.citations == [event_iri("d2")]


def test_missing_human_involvement_is_not_reconstructable() -> None:
    narrative = _narrative(decision("d3", acted_for=[]))
    human = next(s for s in narrative.sentences if s.slot == "human")
    assert human.reconstructable is False
    assert narrative.reconstructable is False


def test_missing_options_is_not_reconstructable() -> None:
    narrative = _narrative(decision("d4", options=[], chosen=None))
    options = next(s for s in narrative.sentences if s.slot == "options")
    assert options.reconstructable is False


def test_every_sentence_is_cited_even_when_unreconstructable() -> None:
    narrative = _narrative(
        decision("d5", ai_role=None, options=[], chosen=None, acted_for=[], used=[])
    )
    assert all(s.citations for s in narrative.sentences)
    assert narrative.reconstructable is False


def test_consequential_filter_and_deterministic_sampling() -> None:
    events = [decision(f"d{i}") for i in range(6)]
    events.append(
        {
            "id": "nd",
            "data": {"@type": "Decision", "decision_type": "credit:AddressChange"},
        }
    )
    store = build_graph(events, domain=DOMAIN)
    try:
        consequential = explanation.consequential_decisions(events, DOMAIN)
        assert (
            len(consequential) == 6
        )  # the AddressChange decision is not consequential
        sample = explanation.render_sample(events, store, DOMAIN, k=3)
        again = explanation.render_sample(list(reversed(events)), store, DOMAIN, k=3)
        assert [n.decision for n in sample] == [
            n.decision for n in again
        ]  # order-independent
        assert len(sample) == 3
    finally:
        store.close()
