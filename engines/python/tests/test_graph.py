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


def _event(event_id: str, ptype: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": event_id,
        "source": "urn:agentce:source:guard",
        "subject": "spiffe://corp/agents/a",
        "time": "t",
        "type": f"org.agent-conformance.evidence.{ptype}.v1",
        "agentcesourceclass": "enforcement_point",
        "data": {"@type": ptype, **data},
    }


def _robust(events: list[dict[str, Any]], decision_id: str = "d1") -> list[str]:
    store = build_graph(events, domain=DOMAIN)
    return store.literal_values(
        event_iri(decision_id), "agentce:robustToUntrustedContent"
    )


def _with_inputs(event: dict[str, Any], inputs: list[str]) -> dict[str, Any]:
    event["data"]["inputs"] = inputs
    return event


def test_robust_when_every_input_is_trusted() -> None:
    write = _event("w1", "MemoryWrite", {"record_ref": "mem:r1", "trust": "trusted"})
    assert _robust([write, _with_inputs(decision("d1", "t"), ["mem:r1"])]) == ["true"]


def test_robust_when_the_decision_used_nothing() -> None:
    assert _robust([decision("d1", "t")]) == ["true"]


def test_not_robust_on_an_input_the_guard_marked_untrusted() -> None:
    for marks in (
        {"trust": "untrusted"},
        {"trust": "quarantined"},
        {"trust": "trusted", "guard_verdict": "quarantine"},
        {"guard_verdict": "block"},
    ):
        write = _event("w1", "MemoryWrite", {"record_ref": "mem:r1", **marks})
        assert _robust([write, _with_inputs(decision("d1", "t"), ["mem:r1"])]) == [
            "false"
        ], marks


def test_sanitized_or_reviewed_records_stay_trusted() -> None:
    write = _event(
        "w1", "MemoryWrite", {"record_ref": "mem:r1", "guard_verdict": "sanitize"}
    )
    assert _robust([write, _with_inputs(decision("d1", "t"), ["mem:r1"])]) == ["true"]


def test_not_robust_through_a_consumed_read() -> None:
    write = _event("w1", "MemoryWrite", {"record_ref": "mem:r1", "trust": "untrusted"})
    read = _event(
        "m1",
        "MemoryRead",
        {"record_refs": ["mem:r1"], "refs": {"consumer": "agentce:event/d1"}},
    )
    assert _robust([write, read, decision("d1", "t")]) == ["false"]
    low = _event(
        "m2",
        "MemoryRead",
        {"trust_min": "untrusted", "refs": {"consumer": "agentce:event/d1"}},
    )
    assert _robust([low, decision("d1", "t")]) == ["false"]
    elsewhere = _event(
        "m3",
        "MemoryRead",
        {"trust_min": "untrusted", "refs": {"consumer": "agentce:event/d2"}},
    )
    assert _robust([elsewhere, decision("d1", "t")]) == ["true"]


def test_not_robust_when_acting_on_an_untrusted_instruction() -> None:
    for source_class, expected in (("tool_output", "false"), ("user", "true")):
        instruction = _event("i1", "Instruction", {"source_class": source_class})
        dec = decision("d1", "t")
        dec["data"]["refs"] = {"instruction": "agentce:event/i1"}
        assert _robust([instruction, dec]) == [expected], source_class


def test_robust_to_untrusted_content_is_independent_of_event_order() -> None:
    write = _event("w1", "MemoryWrite", {"record_ref": "mem:r1", "trust": "untrusted"})
    dec = _with_inputs(decision("d1", "t"), ["mem:r1"])
    assert _robust([dec, write]) == _robust([write, dec]) == ["false"]


def test_malformed_memory_fields_are_ignored() -> None:
    write = _event(
        "w1", "MemoryWrite", {"record_ref": "mem:r1", "trust": ["untrusted"]}
    )
    read = _event(
        "m1",
        "MemoryRead",
        {
            "record_refs": "mem:r1",
            "trust_min": {},
            "refs": {"consumer": "agentce:event/d1"},
        },
    )
    dec = decision("d1", "t")
    dec["data"]["inputs"] = "mem:r1"
    assert _robust([write, read, dec]) == ["true"]


def test_not_robust_on_an_input_naming_an_untrusted_event() -> None:
    read = _event(
        "m1", "MemoryRead", {"record_refs": ["mem:r9"], "trust_min": "untrusted"}
    )
    write = _event("w1", "MemoryWrite", {"record_ref": "mem:r1", "trust": "untrusted"})
    for event in (read, write):
        dec = _with_inputs(decision("d1", "t"), [event_iri(event["id"])])
        assert _robust([event, dec]) == ["false"], event["id"]


def test_input_model_call_counts_through_the_reads_it_consumed() -> None:
    call = _event("mc1", "ModelCall", {})
    for trust_min, expected in (("untrusted", "false"), ("trusted", "true")):
        read = _event(
            "m1",
            "MemoryRead",
            {
                "record_refs": ["mem:r9"],
                "trust_min": trust_min,
                "refs": {"consumer": "agentce:event/mc1"},
            },
        )
        dec = _with_inputs(decision("d1", "t"), ["agentce:event/mc1"])
        assert _robust([read, call, dec]) == [expected], trust_min


def test_taint_follows_content_through_any_number_of_hops() -> None:
    write = _event("w1", "MemoryWrite", {"record_ref": "mem:r1", "trust": "untrusted"})
    tool = _event("tc1", "ToolCall", {"used": ["mem:r1"], "result_ref": "content:t1"})
    call = _event(
        "mc1", "ModelCall", {"input_ref": "content:t1", "output_ref": "content:o1"}
    )
    minor = _with_inputs(decision("d0", "t"), ["content:o1"])
    dec = _with_inputs(decision("d1", "t"), ["agentce:event/d0"])
    events = [write, tool, call, minor, dec]
    assert _robust(events, "d0") == _robust(events, "d1") == ["false"]
    assert _robust(list(reversed(events)), "d1") == ["false"]


def test_taint_reaches_a_decision_through_its_origin() -> None:
    read = _event(
        "m1",
        "MemoryRead",
        {"trust_min": "untrusted", "refs": {"consumer": "agentce:event/mc1"}},
    )
    dec = decision("d1", "t")
    dec["data"]["refs"] = {"origin": "agentce:event/mc1"}
    assert _robust([read, _event("mc1", "ModelCall", {}), dec]) == ["false"]


def test_a_trusted_write_of_a_record_another_write_tainted_is_untrusted() -> None:
    bad = _event("w1", "MemoryWrite", {"record_ref": "mem:r1", "trust": "untrusted"})
    good = _event("w2", "MemoryWrite", {"record_ref": "mem:r1", "trust": "trusted"})
    dec = _with_inputs(decision("d1", "t"), ["agentce:event/w2"])
    assert _robust([bad, good, dec]) == ["false"]


def test_an_instruction_derived_from_an_untrusted_one_is_untrusted() -> None:
    origin = _event("i1", "Instruction", {"source_class": "tool_output"})
    derived = _event(
        "i2",
        "Instruction",
        {"source_class": "user", "refs": {"parent": "agentce:event/i1"}},
    )
    dec = decision("d1", "t")
    dec["data"]["refs"] = {"instruction": "agentce:event/i2"}
    assert _robust([origin, derived, dec]) == ["false"]


def test_a_cycle_of_inputs_ends_and_stays_trusted_without_a_source() -> None:
    first = _with_inputs(decision("d1", "t"), ["agentce:event/d2"])
    second = _with_inputs(decision("d2", "t"), ["agentce:event/d1"])
    assert _robust([first, second]) == ["true"]


def _ruled(events: list[dict[str, Any]], decision_id: str = "d1") -> list[str]:
    store = build_graph(events, domain=DOMAIN)
    return store.literal_values(
        event_iri(decision_id), "agentce:untrustedContentRuledOn"
    )


def _self_report(event: dict[str, Any]) -> dict[str, Any]:
    event["agentcesourceclass"] = "self_report"
    return event


def test_ruled_on_when_the_guard_ruled_on_every_input() -> None:
    write = _event("w1", "MemoryWrite", {"record_ref": "mem:r1", "trust": "trusted"})
    assert _ruled([write, _with_inputs(decision("d1", "t"), ["mem:r1"])]) == ["true"]
    assert _ruled([decision("d1", "t")]) == ["true"]


def test_not_ruled_on_through_a_read_only_the_agent_reported() -> None:
    write = _event("w1", "MemoryWrite", {"record_ref": "mem:r1", "trust": "trusted"})
    read = _self_report(
        _event(
            "m1",
            "MemoryRead",
            {"record_refs": ["mem:r1"], "refs": {"consumer": "agentce:event/d1"}},
        )
    )
    assert _ruled([write, read, decision("d1", "t")]) == ["false"]
    # The guard's own read of the same record does not cover the agent's read.
    guard_read = _event("m2", "MemoryRead", {"record_refs": ["mem:r1"]})
    assert _ruled([write, guard_read, read, decision("d1", "t")]) == ["false"]


def test_not_ruled_on_a_record_no_enforcement_point_covers() -> None:
    unguarded = _self_report(
        _event("w1", "MemoryWrite", {"record_ref": "mem:r1", "trust": "trusted"})
    )
    used = _with_inputs(decision("d1", "t"), ["mem:r1"])
    assert _ruled([unguarded, used]) == ["false"]
    # An enforcement-point write with no trust or verdict is not a ruling either.
    silent = _event("w2", "MemoryWrite", {"record_ref": "mem:r1"})
    assert _ruled([silent, used]) == ["false"]
    # An enforcement-point read of the record covers it only when it filtered by trust.
    guard_read = _event("m1", "MemoryRead", {"record_refs": ["mem:r1"]})
    assert _ruled([unguarded, guard_read, used]) == ["false"]
    filtered = _event(
        "m1", "MemoryRead", {"record_refs": ["mem:r1"], "trust_min": "trusted"}
    )
    assert _ruled([unguarded, filtered, used]) == ["true"]


def test_not_ruled_on_a_ref_the_bundle_does_not_hold() -> None:
    for ref in ("mem:missing", "agentce:event/missing"):
        used = _with_inputs(decision("d1", "t"), [ref])
        assert _ruled([used]) == ["false"], ref
    other = _with_inputs(decision("d2", "t"), ["mem:missing"])
    assert _ruled([decision("d1", "t"), other]) == ["true"]


def test_ruled_on_is_independent_of_event_order() -> None:
    write = _self_report(_event("w1", "MemoryWrite", {"record_ref": "mem:r1"}))
    events = [write, _with_inputs(decision("d1", "t"), ["mem:r1"])]
    assert _ruled(events) == _ruled(list(reversed(events))) == ["false"]


def test_an_untrusted_origin_class_with_no_ruling_taints() -> None:
    for origin, expected in (("tool_output", "false"), ("user", "true")):
        write = _event(
            "w1",
            "MemoryWrite",
            {"record_ref": "mem:r1", "provenance_origin_class": origin},
        )
        used = _with_inputs(decision("d1", "t"), ["mem:r1"])
        assert _robust([write, used]) == [expected], origin
    # The guard's own ruling wins over the origin class.
    ruled = _event(
        "w1",
        "MemoryWrite",
        {
            "record_ref": "mem:r1",
            "provenance_origin_class": "tool_output",
            "trust": "trusted",
        },
    )
    used = _with_inputs(decision("d1", "t"), ["mem:r1"])
    assert _robust([ruled, used]) == ["true"]


def _instruction(event_id: str, source_class: str, **refs: str) -> dict[str, Any]:
    data: dict[str, Any] = {"source_class": source_class}
    if refs:
        data["refs"] = {key: f"agentce:event/{ref}" for key, ref in refs.items()}
    return _event(event_id, "Instruction", data)


def _acts(events: list[dict[str, Any]], acting: str = "tc1") -> list[str]:
    """CND-05's flag on ``acting``, a ToolCall or Decision that acts on instruction ``i1``."""
    store = build_graph(events, domain=DOMAIN)
    return store.literal_values(event_iri(acting), "agentce:actsOnUntrusted")


def _call(event_id: str = "tc1", instruction: str = "i1") -> dict[str, Any]:
    return _event(
        event_id, "ToolCall", {"refs": {"instruction": f"agentce:event/{instruction}"}}
    )


def test_acts_on_untrusted_reads_the_instruction_s_own_class() -> None:
    for source_class, expected in (("tool_output", "true"), ("user", "false")):
        assert _acts([_instruction("i1", source_class), _call()]) == [expected]


def test_acts_on_untrusted_follows_the_parent_chain_for_any_number_of_hops() -> None:
    for root, expected in (("retrieved", "true"), ("service", "false")):
        events = [
            _instruction("i3", root),
            _instruction("i2", "operator", parent="i3"),
            _instruction("i1", "user", parent="i2"),
            _call(),
        ]
        assert _acts(events) == [expected], root
        assert _acts(list(reversed(events))) == [expected], root


def test_acts_on_untrusted_when_the_origin_produced_tool_output_or_retrieved_content() -> (
    None
):
    call = _call("tc0", "i0")
    access = _event("ra0", "ResourceAccess", {"operation": "read"})
    for origin in ("tc0", "ra0"):
        events = [_instruction("i0", "user"), call, access]
        events += [_instruction("i1", "user", origin=origin), _call()]
        assert _acts(events) == ["true"], origin
    assert _acts([_instruction("i0", "user"), call], acting="tc0") == ["false"]


def test_acts_on_untrusted_when_the_origin_read_is_untrusted() -> None:
    write = _event("w1", "MemoryWrite", {"record_ref": "mem:r1", "trust": "trusted"})
    for trust_min, expected in (("untrusted", "true"), ("trusted", "false")):
        read = _event(
            "m1", "MemoryRead", {"record_refs": ["mem:r1"], "trust_min": trust_min}
        )
        events = [write, read, _instruction("i1", "memory_trusted", origin="m1")]
        assert _acts([*events, _call()]) == [expected], trust_min
    bad = _event("w1", "MemoryWrite", {"record_ref": "mem:r1", "trust": "untrusted"})
    read = _event(
        "m1", "MemoryRead", {"record_refs": ["mem:r1"], "trust_min": "trusted"}
    )
    events = [bad, read, _instruction("i1", "memory_trusted", origin="m1"), _call()]
    assert _acts(events) == ["true"]


def test_acts_on_untrusted_ends_on_cycles_and_never_walks_down_the_chain() -> None:
    cycle = [
        _instruction("i1", "user", parent="i2"),
        _instruction("i2", "user", parent="i1"),
        _call(),
    ]
    assert _acts(cycle) == ["false"]
    child = [_instruction("i1", "user"), _instruction("i2", "tool_output", parent="i1")]
    assert _acts([*child, _call()]) == ["false"]


def test_a_decision_acting_on_a_derived_untrusted_instruction_is_flagged() -> None:
    dec = decision("d1", "t")
    dec["data"]["refs"] = {"instruction": "agentce:event/i1"}
    events = [
        _instruction("i0", "tool_output"),
        _instruction("i1", "user", parent="i0"),
    ]
    assert _acts([*events, dec], acting="d1") == ["true"]


def test_acts_on_untrusted_when_the_lineage_names_a_record_the_bundle_does_not_hold() -> (
    None
):
    for key in ("parent", "origin"):
        events = [_instruction("i1", "user", **{key: "gone"}), _call()]
        assert _acts(events) == ["true"], key


def test_acts_on_untrusted_when_the_instruction_declares_no_source_class() -> None:
    bare = _event("i0", "Instruction", {})
    assert _acts([bare, _call(instruction="i0")]) == ["true"]
    events = [bare, _instruction("i1", "user", parent="i0"), _call()]
    assert _acts(events) == ["true"]


def test_acts_on_untrusted_when_the_guard_never_ruled_on_the_origin_read() -> None:
    derived = _instruction("i1", "memory_trusted", origin="m1")
    unruled = _event("m1", "MemoryRead", {"record_refs": ["mem:r9"]})
    unruled["agentcesourceclass"] = "self_report"
    assert _acts([unruled, derived, _call()]) == ["true"]
    ruled = _event(
        "m1", "MemoryRead", {"record_refs": ["mem:r9"], "trust_min": "trusted"}
    )
    assert _acts([ruled, derived, _call()]) == ["false"]


# --- INC-03, OVS-07, OVS-08 and ROB-07 literals ---

ALICE = {"kind": "human", "id": "spiffe://corp/humans/alice"}
BOB = {"kind": "human", "id": "spiffe://corp/humans/bob"}
SERVICE = {"kind": "service", "id": "spiffe://corp/services/ops"}


def _flag(events: list[dict[str, Any]], node: str, predicate: str) -> list[str]:
    store = build_graph(events, domain=DOMAIN)
    return store.literal_values(event_iri(node), f"agentce:{predicate}")


def _incident(
    event_id: str, related: list[str] | None = None, **data: Any
) -> dict[str, Any]:
    body: dict[str, Any] = dict(data)
    if related is not None:
        body["related_refs"] = related
    return _event(event_id, "Incident", body)


def _approval(event_id: str, actor: dict[str, str]) -> dict[str, Any]:
    return _event(
        event_id,
        "ApprovalDecided",
        {"refs": {"decision": event_iri("d1")}, "actor": actor},
    )


def _override(
    event_id: str,
    *,
    original: Any = "deny",
    replacement: Any = "approve",
    actor: dict[str, str] = ALICE,
    decision_id: str = "d1",
) -> dict[str, Any]:
    return _event(
        event_id,
        "Override",
        {
            "refs": {"decision": event_iri(decision_id)},
            "original": original,
            "replacement": replacement,
            "actor": actor,
        },
    )


def _interrupt(
    event_id: str, effect: str, mechanism: str | None, actor: dict[str, str] = ALICE
) -> dict[str, Any]:
    data: dict[str, Any] = {"effect": effect, "actor": actor}
    if mechanism is not None:
        data["mechanism"] = mechanism
    return _event(event_id, "Interrupt", data)


def test_triggers_incident_on_a_directly_named_decision_only() -> None:
    events = [
        decision("d1", "t"),
        decision("d2", "t"),
        _incident("x1", [event_iri("d1")]),
    ]
    assert _flag(events, "d1", "triggersIncident") == ["true"]
    assert _flag(events, "d2", "triggersIncident") == ["false"]


def test_triggers_incident_through_the_named_override_s_decision() -> None:
    events = [
        decision("d1", "t"),
        decision("d2", "t"),
        _override("o1"),
        _incident("x1", [event_iri("o1")]),
    ]
    assert _flag(events, "d1", "triggersIncident") == ["true"]
    assert _flag(events, "d2", "triggersIncident") == ["false"]


def test_triggers_incident_through_refs_decision() -> None:
    incident = _incident("x1")
    incident["data"]["refs"] = {"decision": event_iri("d2")}
    events = [decision("d1", "t"), decision("d2", "t"), incident]
    assert _flag(events, "d1", "triggersIncident") == ["false"]
    assert _flag(events, "d2", "triggersIncident") == ["true"]


def test_an_incident_with_no_names_holds_every_decision() -> None:
    events = [decision("d1", "t"), decision("d2", "t"), _incident("x1")]
    assert _flag(events, "d1", "triggersIncident") == ["true"]
    assert _flag(events, "d2", "triggersIncident") == ["true"]


def test_one_unresolved_name_beside_a_resolved_one_holds_every_decision() -> None:
    events = [
        decision("d1", "t"),
        decision("d2", "t"),
        _incident("x1", [event_iri("d1"), event_iri("missing")]),
    ]
    assert _flag(events, "d2", "triggersIncident") == ["true"]


def test_no_incident_triggers_no_decision() -> None:
    assert _flag([decision("d1", "t")], "d1", "triggersIncident") == ["false"]


def test_oversight_coverage_needs_one_human_reviewer() -> None:
    assert _flag([decision("d1", "t")], "d1", "oversightCoverageComplete") == ["false"]
    events = [decision("d1", "t"), _approval("a1", ALICE)]
    assert _flag(events, "d1", "oversightCoverageComplete") == ["true"]
    events = [decision("d1", "t"), _approval("a1", SERVICE)]
    assert _flag(events, "d1", "oversightCoverageComplete") == ["false"]


def test_dual_control_counts_the_same_human_once() -> None:
    events = [
        decision("d1", "t", modality="dual_control"),
        _approval("a1", ALICE),
        _approval("a2", ALICE),
    ]
    assert _flag(events, "d1", "oversightCoverageComplete") == ["false"]


def test_dual_control_is_covered_by_two_humans() -> None:
    events = [
        decision("d1", "t", modality="dual_control"),
        _approval("a1", ALICE),
        _approval("a2", BOB),
    ]
    assert _flag(events, "d1", "oversightCoverageComplete") == ["true"]


def test_domain_declared_dual_control_needs_two_humans() -> None:
    dual = DomainBinding.from_dict(
        {
            "decision_types": [
                {
                    "id": CREDIT,
                    "subclass_of": "agentce:ConsequentialDecision",
                    "consequential": True,
                    "required_oversight_modality": "dual_control",
                }
            ]
        }
    )
    events = [decision("d1", "t"), _approval("a1", ALICE)]
    store = build_graph(events, domain=dual)
    assert store.literal_values(
        event_iri("d1"), "agentce:oversightCoverageComplete"
    ) == ["false"]


def test_override_is_effective_with_a_differing_replacement() -> None:
    events = [decision("d1", "t"), _override("o1")]
    assert _flag(events, "o1", "interventionEffective") == ["true"]
    assert _flag(events, "o1", "interventionByHuman") == ["true"]


def test_override_replacement_equal_to_original_is_not_effective() -> None:
    events = [decision("d1", "t"), _override("o1", replacement="deny")]
    assert _flag(events, "o1", "interventionEffective") == ["false"]


def test_override_of_a_decision_not_held_is_not_effective() -> None:
    events = [decision("d1", "t"), _override("o1", decision_id="missing")]
    assert _flag(events, "o1", "interventionEffective") == ["false"]


def test_interrupt_is_effective_when_a_mechanism_halts_or_pauses() -> None:
    for effect in ("halted", "paused"):
        events = [_interrupt("i1", effect, "kill_switch")]
        assert _flag(events, "i1", "interventionEffective") == ["true"]
        assert _flag(events, "i1", "interventionByHuman") == ["true"]


def test_interrupt_degraded_or_without_a_mechanism_is_not_effective() -> None:
    for effect, mechanism in (("degraded", "stop_button"), ("halted", None)):
        events = [_interrupt("i1", effect, mechanism)]
        assert _flag(events, "i1", "interventionEffective") == ["false"]


def test_intervention_by_a_service_actor_is_not_by_a_human() -> None:
    events = [decision("d1", "t"), _override("o1", actor=SERVICE)]
    assert _flag(events, "o1", "interventionByHuman") == ["false"]
    events = [_interrupt("i1", "halted", "manual", actor=SERVICE)]
    assert _flag(events, "i1", "interventionByHuman") == ["false"]


def test_incident_responded_needs_detection_and_a_response() -> None:
    responded = _incident(
        "x1", detected_at="2026-01-01T00:00:00Z", reported_at="2026-01-02T00:00:00Z"
    )
    assert _flag([responded], "x1", "incidentResponded") == ["true"]
    detected_only = _incident("x1", detected_at="2026-01-01T00:00:00Z")
    assert _flag([detected_only], "x1", "incidentResponded") == ["false"]
    undetected = _incident("x1", reported_at="2026-01-02T00:00:00Z")
    assert _flag([undetected], "x1", "incidentResponded") == ["false"]
