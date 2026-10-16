"""The graph builder: typing with closure, relations, and the materialised glue edges (SPEC §6.3)."""

from __future__ import annotations

from typing import Any

from agentce.domain import DomainBinding
from agentce.graph import build_graph, flagged_records
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
        "data": {
            "@type": "ApprovalDecided",
            "refs": {"decision": "agentce:event/d1"},
            "actor": ALICE,
            "session_ref": _session(ALICE),
        },
    }
    later = decision("d2", "2026-01-01T01:00:00Z")
    store = build_graph([earlier, approval, later, *LOGINS], domain=DOMAIN)
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
    # A decision nothing recorded flows into used nothing the guard could rule on (ruling 15, 18.127).
    assert _ruled([decision("d1", "t")]) == ["false"]


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
    write = _event("w1", "MemoryWrite", {"record_ref": "mem:r1", "trust": "trusted"})
    used = _with_inputs(decision("d1", "t"), ["mem:r1"])
    assert _ruled([write, used, other]) == ["true"]


def test_ruled_on_is_independent_of_event_order() -> None:
    write = _self_report(_event("w1", "MemoryWrite", {"record_ref": "mem:r1"}))
    events = [write, _with_inputs(decision("d1", "t"), ["mem:r1"])]
    assert _ruled(events) == _ruled(list(reversed(events))) == ["false"]


def test_an_unruled_rewrite_of_a_guarded_record_is_not_ruled_on() -> None:
    ruled = _event("w1", "MemoryWrite", {"record_ref": "mem:r1", "trust": "trusted"})
    used = _with_inputs(decision("d1", "t"), ["mem:r1"])
    rewrites = (
        _self_report(_event("w2", "MemoryWrite", {"record_ref": "mem:r1"})),
        _self_report(
            _event("w2", "MemoryWrite", {"record_ref": "mem:r1", "trust": "trusted"})
        ),
        _event("w2", "MemoryWrite", {"record_ref": "mem:r1"}),
    )
    for rewrite in rewrites:
        assert _ruled([ruled, rewrite, used]) == ["false"], rewrite
        assert _ruled([rewrite, used, ruled]) == ["false"], rewrite
        # The guard's read filtered by trust rules on what the decision used.
        filtered = _event(
            "m1", "MemoryRead", {"record_refs": ["mem:r1"], "trust_min": "trusted"}
        )
        assert _ruled([ruled, rewrite, filtered, used]) == ["true"], rewrite
    # Two ruled enforcement-point writes stay ruled on.
    again = _event(
        "w2", "MemoryWrite", {"record_ref": "mem:r1", "guard_verdict": "allow"}
    )
    assert _ruled([ruled, again, used]) == ["true"]


def test_a_record_another_subject_flagged_is_never_ruled_on() -> None:
    write = _event("w1", "MemoryWrite", {"record_ref": "mem:r1", "trust": "trusted"})
    filtered = _event(
        "m1",
        "MemoryRead",
        {
            "record_refs": ["mem:r1"],
            "trust_min": "trusted",
            "refs": {"consumer": "agentce:event/d1"},
        },
    )
    events = [write, filtered, decision("d1", "t")]
    assert _ruled(events) == ["true"]
    for foreign, expected in (
        (frozenset({"mem:r1"}), "false"),
        (frozenset({"mem:r2"}), "true"),
    ):
        store = build_graph(events, domain=DOMAIN, foreign_untrusted=foreign)
        got = store.literal_values(event_iri("d1"), "agentce:untrustedContentRuledOn")
        assert got == [expected], foreign
        assert store.literal_values(
            event_iri("d1"), "agentce:robustToUntrustedContent"
        ) == ["true"]


def test_a_guard_read_rules_on_its_records_only_at_trust_min_trusted() -> None:
    # 18.172: a read that let untrusted or quarantined records through, or filtered by nothing, rules on nothing.
    unguarded = _self_report(
        _event("w1", "MemoryWrite", {"record_ref": "mem:r1", "trust": "trusted"})
    )
    used = _with_inputs(decision("d1", "t"), ["mem:r1"])
    for trust_min, expected in (
        ("trusted", "true"),
        ("untrusted", "false"),
        ("quarantined", "false"),
        (None, "false"),
    ):
        read = _event(
            "m1", "MemoryRead", {"record_refs": ["mem:r1"], "trust_min": trust_min}
        )
        assert _ruled([unguarded, read, used]) == [expected], trust_min
        assert _ruled([used, read, unguarded]) == [expected], trust_min
    # A ruled enforcement-point write still rules on its record whatever the read let through.
    ruled = _event("w1", "MemoryWrite", {"record_ref": "mem:r1", "trust": "trusted"})
    read = _event(
        "m1", "MemoryRead", {"record_refs": ["mem:r1"], "trust_min": "untrusted"}
    )
    assert _ruled([ruled, read, used]) == ["true"]


def test_a_record_another_subjects_unreadable_write_may_have_changed_is_not_ruled_on() -> (
    None
):
    # 18.172 (SPEC §7.4): the record it names; with no readable record_ref, every record of its store, a record no
    # event names a store for counting as one of them; with no readable store either, every record.
    def build(events: list[dict[str, Any]], unreadable: Any) -> Any:
        return build_graph(events, domain=DOMAIN, foreign_unreadable=unreadable)

    used = _with_inputs(decision("d1", "t"), ["mem:r1"])
    for store in ("kb", None):
        data = {"record_ref": "mem:r1", "trust": "trusted", "store": store}
        events = [_event("w1", "MemoryWrite", data), used]
        for unreadable, expected in (
            ((), "true"),
            ((("mem:r1", None),), "false"),
            ((("mem:r1", "other"),), "false"),
            ((("mem:r2", None),), "true"),
            (((None, "kb"),), "false"),
            (((None, "other"),), "false" if store is None else "true"),
            (((None, None),), "false"),
            ((("mem:r2", None), (None, None)), "false"),
        ):
            for order in (events, list(reversed(events))):
                graph = build(order, unreadable)
                got = graph.literal_values(
                    event_iri("d1"), "agentce:untrustedContentRuledOn"
                )
                assert got == [expected], (store, unreadable)
                # Nobody could read that content, so it is unruled, never untrusted.
                assert graph.literal_values(
                    event_iri("d1"), "agentce:robustToUntrustedContent"
                ) == ["true"]


def test_flagged_records_names_every_subject_that_flagged_a_record() -> None:
    def write(event_id: str, subject: str, **data: Any) -> dict[str, Any]:
        event = _event(event_id, "MemoryWrite", {"record_ref": "mem:r1", **data})
        event["subject"] = subject
        return event

    events = [
        write("w1", "a", trust="untrusted"),
        write("w2", "b", guard_verdict="block"),
        write("w3", "c", trust="trusted"),
        write("w4", "d", provenance_origin_class="tool_output"),
        write("w5", "e", provenance_origin_class="tool_output", trust="trusted"),
        _event(
            "m1", "MemoryRead", {"record_refs": ["mem:r1"], "trust_min": "untrusted"}
        ),
    ]
    events.append(_self_report(write("w6", "f", trust="trusted")))
    assert flagged_records(events) == {"mem:r1": {"a", "b", "d", "f"}}
    assert flagged_records(reversed(events)) == flagged_records(events)


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


def _judged(events: list[dict[str, Any]], acting: str = "tc1") -> list[str]:
    """CND-05's evidence flag on ``acting``: whether the records show which instruction it acted on."""
    store = build_graph(events, domain=DOMAIN)
    return store.literal_values(event_iri(acting), "agentce:actsOnJudged")


def test_cnd05_a_tool_call_naming_no_instruction_is_not_judged() -> None:
    absent = _event("tc1", "ToolCall", {"refs": {}})
    null = _event("tc1", "ToolCall", {"refs": {"instruction": None}})
    for call in (absent, null):
        assert _judged([_instruction("i1", "user"), call]) == ["false"]
        assert _acts([_instruction("i1", "user"), call]) == ["false"]


def test_cnd05_a_tool_call_on_a_held_trusted_instruction_is_judged() -> None:
    assert _judged([_instruction("i1", "user"), _call()]) == ["true"]


def test_cnd05_a_decision_naming_no_instruction_is_judged() -> None:
    assert _judged([decision("d1", "t")], acting="d1") == ["true"]


# --- INC-03, OVS-07, OVS-08 and ROB-07 literals ---

# Reviewers outside the decisions' delegation chain (acted_for alice), each with an identity-provider login.
ALICE = {"kind": "human", "id": "spiffe://corp/reviewers/alice"}
BOB = {"kind": "human", "id": "spiffe://corp/reviewers/bob"}
IDP = "urn:example:service:identity-provider"


def _login(
    event_id: str,
    source_class: str = "independent_system",
    agent: str = IDP,
    principal: dict[str, str] | None = None,
) -> dict[str, Any]:
    """An identity-provider SessionStart a session_ref can name, naming the human it authenticated (SPEC §10.4);
    by default the reviewer its id names."""
    if principal is None:
        principal = {
            "kind": "human",
            "id": "spiffe://corp/reviewers/" + event_id.removeprefix("login-"),
        }
    event = _event(
        event_id, "SessionStart", {"agent": {"id": agent}, "principal": principal}
    )
    event["agentcesourceclass"] = source_class
    return event


def _session(actor: dict[str, str]) -> str:
    return event_iri("login-" + actor["id"].rsplit("/", 1)[-1])


LOGINS = [_login("login-alice"), _login("login-bob")]
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


def _approval(
    event_id: str, actor: dict[str, str], outcome: str | None = "approve"
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "refs": {"decision": event_iri("d1")},
        "actor": actor,
        "session_ref": _session(actor),
    }
    if outcome is not None:
        data["outcome"] = outcome
    return _event(event_id, "ApprovalDecided", data)


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
            "session_ref": _session(actor),
        },
    )


def _interrupt(
    event_id: str, effect: str, mechanism: str | None, actor: dict[str, str] = ALICE
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "effect": effect,
        "actor": actor,
        "session_ref": _session(actor),
    }
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
    events = [decision("d1", "t"), _approval("a1", ALICE), *LOGINS]
    assert _flag(events, "d1", "oversightCoverageComplete") == ["true"]
    events = [decision("d1", "t"), _approval("a1", SERVICE)]
    assert _flag(events, "d1", "oversightCoverageComplete") == ["false"]


def test_only_an_approve_outcome_approves() -> None:
    """A reject, an edit or no outcome is a review (agentce:reviewedBy; OVS-01, INC-03, RSK-02) but never an approval
    (agentce:approvedBy; CND-02, OVS-08)."""
    d1 = event_iri("d1")
    for outcome, approved in (
        ("approve", True),
        ("reject", False),
        ("edit", False),
        (None, False),
    ):
        events = [decision("d1", "t"), _approval("a1", ALICE, outcome), *LOGINS]
        store = build_graph(events, domain=DOMAIN)
        assert store.objects(d1, "agentce:reviewedBy") == [event_iri("a1")]
        assert store.objects(d1, "agentce:approvedBy") == (
            [event_iri("a1")] if approved else []
        )
        assert _flag(events, "d1", "oversightCoverageComplete") == [
            str(approved).lower()
        ]


def test_an_approve_after_the_action_ran_is_a_review_not_an_approval() -> None:
    """An approve gates the decision (agentce:approvedBy; CND-02, OVS-08) only when it is recorded at or before every
    ToolCall that executes it, compared as instants; a later one is still a review (agentce:reviewedBy)."""
    d1 = event_iri("d1")
    for approved_at, approved in (
        ("2026-01-10T00:00:00Z", True),
        ("2026-01-10T01:00:00Z", True),
        ("2026-01-10T01:00:00.001Z", False),
        ("2026-01-10T00:30:00-05:00", False),
        ("2026-01-10T05:00:00+04:00", True),
        ("2026-01-10T00:30:00", False),
    ):
        action = _event("t0", "ToolCall", {"refs": {"decision": d1}})
        action["time"] = "2026-01-10T01:00:00Z"
        approval = {**_approval("a1", ALICE), "time": approved_at}
        events = [decision("d1", "2026-01-10T00:00:00Z"), action, approval, *LOGINS]
        store = build_graph(events, domain=DOMAIN)
        assert store.objects(d1, "agentce:reviewedBy") == [event_iri("a1")]
        assert store.objects(d1, "agentce:approvedBy") == (
            [event_iri("a1")] if approved else []
        ), approved_at
        assert _flag(events, "d1", "oversightCoverageComplete") == [
            str(approved).lower()
        ]


def test_an_approve_after_the_outcome_is_a_review_not_an_approval() -> None:
    """An Outcome that names the decision shows it had taken effect (18.144): an approve gates it only when recorded at
    or before every such Outcome too, compared as instants, wherever the Outcome sits in the input."""
    d1 = event_iri("d1")
    for approved_at, approved in (
        ("2026-01-10T01:00:00Z", True),
        ("2026-01-10T01:00:01Z", False),
        ("2026-01-10T00:30:00-05:00", False),
        ("2026-01-10T00:30:00", False),
    ):
        outcome = _event("o1", "Outcome", {"refs": {"decision": d1}})
        outcome["time"] = "2026-01-10T01:00:00Z"
        approval = {**_approval("a1", ALICE), "time": approved_at}
        events = [decision("d1", "2026-01-10T00:00:00Z"), approval, *LOGINS, outcome]
        store = build_graph(events, domain=DOMAIN)
        assert store.objects(d1, "agentce:reviewedBy") == [event_iri("a1")]
        assert store.objects(d1, "agentce:approvedBy") == (
            [event_iri("a1")] if approved else []
        ), approved_at


def test_a_bare_id_names_the_decision() -> None:
    """The approval bound reads every refs.decision as the held decision it names (18.148): an Outcome naming the
    decision by bare id or padded bounds an approve that names it by IRI, an approve naming it by bare id approves it,
    and an Outcome naming no held decision bounds every decision's approvals."""
    d1 = event_iri("d1")
    for outcome_ref, approval_ref, approved in (
        ("d1", d1, False),
        (f"{d1} ", d1, False),
        ("\u00a0d1", d1, False),
        ("AGENTCE:EVENT/D1", d1, False),
        ("a1", d1, False),
        (d1, "d1", False),
        (None, "d1", True),
        (None, f" {d1}", True),
    ):
        approval = _approval("a1", ALICE)
        approval["data"]["refs"]["decision"] = approval_ref
        approval["time"] = "2026-01-10T05:00:00Z"
        events = [decision("d1", "2026-01-10T00:00:00Z"), approval, *LOGINS]
        if outcome_ref is not None:
            outcome = _event("o1", "Outcome", {"refs": {"decision": outcome_ref}})
            outcome["time"] = "2026-01-10T01:00:00Z"
            events.append(outcome)
        store = build_graph(events, domain=DOMAIN)
        assert store.objects(d1, "agentce:reviewedBy") == [event_iri("a1")]
        assert store.objects(d1, "agentce:approvedBy") == (
            [event_iri("a1")] if approved else []
        ), (outcome_ref, approval_ref)


def test_a_null_decision_ref_bounds_every_approval() -> None:
    """A present null refs.decision names no held decision, so an Outcome carrying it before the approve bounds it;
    an Outcome with no refs.decision key, or no refs, is no decision's effect and bounds nothing (18.157)."""
    d1 = event_iri("d1")
    for refs, approved in (({"decision": None}, False), ({}, True), (None, True)):
        approval = _approval("a1", ALICE)
        approval["time"] = "2026-01-10T05:00:00Z"
        outcome = _event("o1", "Outcome", {} if refs is None else {"refs": refs})
        outcome["time"] = "2026-01-10T01:00:00Z"
        events = [decision("d1", "2026-01-10T00:00:00Z"), approval, outcome, *LOGINS]
        store = build_graph(events, domain=DOMAIN)
        assert store.objects(d1, "agentce:approvedBy") == (
            [event_iri("a1")] if approved else []
        ), refs


def test_a_bare_decision_ref_links_every_decision_edge() -> None:
    """Every glue read of refs.decision resolves it as approvedBy does (18.156): records naming the decision by bare id
    or padded, read before the Decision itself, still build each edge to its IRI and mark the ToolCall as executing a
    consequential decision; a name for no held Decision links none."""
    d1 = event_iri("d1")
    linked = [
        _event("t0", "ToolCall", {"refs": {"decision": "d1"}}),
        _event("o1", "Outcome", {"refs": {"decision": " d1"}}),
        _event("v1", "Override", {"refs": {"decision": f"{d1}\t"}}),
        _event("x1", "Interrupt", {"refs": {"decision": "d1"}}),
        _event("n1", "Notice", {"refs": {"decision": "\u00a0d1 "}}),
        _event("t1", "ToolCall", {"refs": {"decision": "nope"}}),
    ]
    store = build_graph(
        [*linked, decision("d1", "2026-01-10T00:00:00Z")], domain=DOMAIN
    )
    assert store.objects(event_iri("t0"), "agentce:executes") == [d1]
    for predicate, node in (
        ("agentce:resultedIn", "o1"),
        ("agentce:overriddenBy", "v1"),
        ("agentce:interruptedBy", "x1"),
        ("agentce:notifiedBy", "n1"),
    ):
        assert store.objects(d1, predicate) == [event_iri(node)], predicate
    assert store.literal_values(event_iri("t0"), "agentce:executesConsequential") == [
        "true"
    ]
    assert store.literal_values(event_iri("t1"), "agentce:executesConsequential") == [
        "false"
    ]
    assert store.objects(event_iri("t1"), "agentce:executes") == ["nope"]


def test_a_later_reject_by_the_same_human_withdraws_the_approval() -> None:
    """Each human's most recent decision before the action governs (18.142): the same human's reject or edit at or
    after an approve, or at a time that cannot be placed against it, withdraws it unless it came after every ToolCall;
    another human's reject does not."""
    d1 = event_iri("d1")
    for actor, outcome, decided_at, approved in (
        (ALICE, "reject", "2026-01-10T02:00:00Z", False),
        (ALICE, "edit", "2026-01-10T02:00:00Z", False),
        (ALICE, "reject", "2026-01-10T01:00:00Z", False),
        (ALICE, "reject", "2026-01-10T03:00:00Z", False),
        (ALICE, "reject", "2026-01-10T04:00:00Z", True),
        (ALICE, "reject", "2026-01-10T02:00:00", False),
        (ALICE, "reject", "2026-01-10T03:30:00+02:00", False),
        (ALICE, "reject", "2026-01-10T00:30:00Z", True),
        (BOB, "reject", "2026-01-10T02:00:00Z", True),
    ):
        action = _event("t0", "ToolCall", {"refs": {"decision": d1}})
        action["time"] = "2026-01-10T03:00:00Z"
        approval = {**_approval("a1", ALICE), "time": "2026-01-10T01:00:00Z"}
        later = {**_approval("a2", actor, outcome), "time": decided_at}
        events = [
            decision("d1", "2026-01-10T00:00:00Z"),
            action,
            approval,
            later,
            *LOGINS,
        ]
        store = build_graph(events, domain=DOMAIN)
        assert store.objects(d1, "agentce:reviewedBy") == [
            event_iri("a1"),
            event_iri("a2"),
        ]
        assert store.objects(d1, "agentce:approvedBy") == (
            [event_iri("a1")] if approved else []
        ), (outcome, decided_at)


def test_the_same_humans_reject_withdraws_even_when_it_fails_the_human_actor_rule() -> (
    None
):
    """A human's own reject withdraws that human's approve even when it names no login or another human's login
    (18.142, fail closed); it is still no review."""
    d1 = event_iri("d1")
    for login in (None, _session(BOB)):
        later = _approval("a2", ALICE, "reject")
        later["time"] = "2026-01-10T02:00:00Z"
        if login is None:
            del later["data"]["session_ref"]
        else:
            later["data"]["session_ref"] = login
        events = [
            decision("d1", "2026-01-10T00:00:00Z"),
            {**_approval("a1", ALICE), "time": "2026-01-10T01:00:00Z"},
            later,
            *LOGINS,
        ]
        store = build_graph(events, domain=DOMAIN)
        assert store.objects(d1, "agentce:reviewedBy") == [event_iri("a1")], login
        assert store.objects(d1, "agentce:approvedBy") == [], login


def test_dual_control_counts_the_same_human_once() -> None:
    events = [
        decision("d1", "t", modality="dual_control"),
        _approval("a1", ALICE),
        _approval("a2", ALICE),
        *LOGINS,
    ]
    assert _flag(events, "d1", "oversightCoverageComplete") == ["false"]


def test_dual_control_is_covered_by_two_humans() -> None:
    events = [
        decision("d1", "t", modality="dual_control"),
        _approval("a1", ALICE),
        _approval("a2", BOB),
        *LOGINS,
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
    events = [decision("d1", "t"), _override("o1"), *LOGINS]
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
        events = [_interrupt("i1", effect, "kill_switch"), *LOGINS]
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


def test_not_waivable_set_lists_failing_oversight_records_and_the_decision_they_name() -> (
    None
):
    """18.186: the store lists what the deviation register cannot waive (SPEC §10.4): each failing ApprovalDecided,
    Override and Interrupt, and the held decision a failing approval names; a passing approval adds nothing."""
    d1 = event_iri("d1")
    store = build_graph([decision("d1", "t"), _approval("a1", ALICE)], domain=DOMAIN)
    assert store.human_actor_failed == {event_iri("a1"), d1}
    store = build_graph(
        [decision("d1", "t"), _approval("a1", ALICE), *LOGINS], domain=DOMAIN
    )
    assert store.human_actor_failed == set()
    events = [
        decision("d1", "t"),
        _override("o1", actor=SERVICE),
        _interrupt("i1", "halted", "manual", actor=SERVICE),
    ]
    assert build_graph(events, domain=DOMAIN).human_actor_failed == {
        event_iri("o1"),
        event_iri("i1"),
    }
    # A failing approval naming no held decision adds only itself.
    orphan = _approval("a1", ALICE)
    orphan["data"]["refs"] = {"decision": "agentce:event/nowhere"}
    assert build_graph(
        [decision("d1", "t"), orphan], domain=DOMAIN
    ).human_actor_failed == {event_iri("a1")}


def test_incident_responded_needs_detection_and_a_response() -> None:
    responded = _incident(
        "x1", detected_at="2026-01-01T00:00:00Z", reported_at="2026-01-02T00:00:00Z"
    )
    assert _flag([responded], "x1", "incidentResponded") == ["true"]
    detected_only = _incident("x1", detected_at="2026-01-01T00:00:00Z")
    assert _flag([detected_only], "x1", "incidentResponded") == ["false"]
    undetected = _incident("x1", reported_at="2026-01-02T00:00:00Z")
    assert _flag([undetected], "x1", "incidentResponded") == ["false"]


def _authorized(ref: Any, key: str = "authorization") -> dict[str, Any]:
    event = decision("d1", "t")
    event["data"]["refs"] = {key: ref}
    return event


def test_risk_reviewed_by_a_review_or_a_held_policy_decision() -> None:
    policy = _event("p1", "PolicyDecision", {"decision": "allow"})
    assert _flag(
        [decision("d1", "t"), _approval("a1", ALICE), *LOGINS], "d1", "riskReviewed"
    ) == ["true"]
    assert _flag([policy, _authorized(event_iri("p1"))], "d1", "riskReviewed") == [
        "true"
    ]
    assert _flag(
        [policy, _authorized(event_iri("p1"), "request")], "d1", "riskReviewed"
    ) == ["true"]
    assert _flag([decision("d1", "t")], "d1", "riskReviewed") == ["false"]


def test_risk_reviewed_needs_the_authorization_to_name_a_held_policy_decision() -> None:
    policy = _event("p1", "PolicyDecision", {"decision": "allow"})
    outcome = _event("o1", "Outcome", {"refs": {"decision": event_iri("d1")}})
    for events in (
        [policy, _authorized(event_iri("missing"))],
        [outcome, _authorized(event_iri("o1"))],
        [policy, _authorized("p1")],
        [policy, _authorized([event_iri("p1")])],
    ):
        assert _flag(events, "d1", "riskReviewed") == ["false"]


def test_human_actor_rule() -> None:
    """SPEC §10.4: an approval or override counts as a human's only when its actor's session_ref names a held
    identity-provider login of someone other than the agent, from an independent or enforcement-point stream, and the
    actor is outside the delegation chain; under dual control one login or one id is one human."""

    def reviewed(*events: dict[str, Any]) -> list[str]:
        return _flag([decision("d1", "t"), *events], "d1", "oversightCoverageComplete")

    assert reviewed(_approval("a1", ALICE), *LOGINS) == ["true"]
    assert reviewed(_approval("a1", ALICE)) == ["false"]  # the login is not held
    assert reviewed(_approval("a1", ALICE), _login("login-alice", "self_report")) == [
        "false"
    ]
    assert reviewed(
        _approval("a1", ALICE), _login("login-alice", agent="spiffe://corp/agents/a")
    ) == ["false"]
    tool_call = _event("login-alice", "ToolCall", {"tool": {"name": "x"}})
    assert reviewed(_approval("a1", ALICE), tool_call) == ["false"]
    owner = {
        "kind": "human",
        "id": "spiffe://corp/humans/alice",
    }  # the decision acts for alice
    assert reviewed(_approval("a1", owner), _login("login-alice", principal=owner)) == [
        "false"
    ]
    # The login names someone else, or nobody: it is not the actor's own.
    assert reviewed(_approval("a1", ALICE), _login("login-alice", principal=BOB)) == [
        "false"
    ]
    unnamed = _login("login-alice")
    del unnamed["data"]["principal"]
    assert reviewed(_approval("a1", ALICE), unnamed) == ["false"]
    # Ids compare after trimming one fixed whitespace set from both ends.
    padded = {"kind": "human", "id": "\u00a0" + ALICE["id"] + "\ufeff"}
    assert reviewed(
        _approval("a1", ALICE), _login("login-alice", principal=padded)
    ) == ["true"]
    no_session = _approval("a1", ALICE)
    del no_session["data"]["session_ref"]
    assert reviewed(no_session, *LOGINS) == ["false"]
    events = [
        decision("d1", "t"),
        _override("o1", actor=owner),
        _login("login-alice", principal=owner),
    ]
    assert _flag(events, "o1", "interventionByHuman") == ["false"]
    # Dual control: two ids on one login, or one id on two logins, are one human.
    dual = decision("d1", "t", modality="dual_control")
    alias = {"kind": "human", "id": ALICE["id"] + " "}
    one_login = [dual, _approval("a1", ALICE), _approval("a2", alias), *LOGINS]
    one_login[2]["data"]["session_ref"] = _session(ALICE)
    assert _flag(one_login, "d1", "oversightCoverageComplete") == ["false"]
    two_logins = [dual, _approval("a1", ALICE), _approval("a2", ALICE), *LOGINS]
    two_logins[2]["data"]["session_ref"] = _session(BOB)
    assert _flag(two_logins, "d1", "oversightCoverageComplete") == ["false"]
    # File order never matters: the login after the approval still counts.
    assert _flag(
        [*LOGINS, _approval("a1", ALICE), decision("d1", "t")],
        "d1",
        "oversightCoverageComplete",
    ) == ["true"]


_SCOPES = DomainBinding.from_dict(
    {
        "task_scopes": [
            {
                "task_type": "review",
                "allowed_tools": ["lookup"],
                "allowed_effect_classes": ["read"],
            },
            {
                "task_type": "pay",
                "allowed_tools": ["pay"],
                "allowed_effect_classes": ["spend"],
            },
        ]
    }
)


def _scoped_call(eid: str, tool: str, effect: str, **extra: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "id": eid,
        "source": "urn:src:gw",
        "subject": "spiffe://corp/agents/a",
        "type": "org.agent-conformance.evidence.ToolCall.v1",
        "data": {"@type": "ToolCall", "tool": {"name": tool}, "effect_class": effect},
    }
    if "refs_task" in extra:
        event["data"]["refs"] = {"task": extra.pop("refs_task")}
    event.update(extra)
    return event


def test_task_scope_within_scope() -> None:
    calls = [
        _scoped_call("in", "lookup", "read", agentcetask="review"),
        _scoped_call("effect", "lookup", "irreversible", agentcetask="review"),
        _scoped_call("tool", "pay", "read", refs_task="review"),
        _scoped_call("unknown", "lookup", "read", agentcetask="task-1"),
        _scoped_call("differ", "lookup", "read", agentcetask="pay", refs_task="review"),
        _scoped_call("untasked", "ping", "irreversible"),
    ]
    store = build_graph(calls, domain=_SCOPES)
    got = {
        c["id"]: store.literal_values(event_iri(c["id"]), "agentce:withinScope")
        for c in calls
    }
    assert {k: v[0] for k, v in got.items()} == {
        "in": "true",
        "effect": "false",
        "tool": "false",
        "unknown": "false",
        "differ": "false",
        "untasked": "true",
    }


def _model_call(eid: str, task: str | None, usage: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "id": eid,
        "source": "urn:src:agent",
        "subject": "spiffe://corp/agents/a",
        "type": "org.agent-conformance.evidence.ModelCall.v1",
        "data": {"@type": "ModelCall", "usage": usage},
    }
    if task is not None:
        event["agentcetask"] = task
    return event


def _budget_flags(
    events: list[dict[str, Any]], budgets: Any, **binding: Any
) -> dict[str, tuple[str, bool]]:
    """Each action's withinBudget and whether the records could tell (no agentce:BudgetUnjudged type)."""
    entry: dict[str, Any] = {
        "task_type": "review",
        "allowed_effect_classes": ["read", "spend"],
    }
    if budgets is not None:
        entry["budgets"] = budgets
    store = build_graph(
        events, domain=DomainBinding.from_dict({"task_scopes": [entry], **binding})
    )
    return {
        e["id"]: (
            store.literal_values(event_iri(e["id"]), "agentce:withinBudget")[0],
            not store.is_a(event_iri(e["id"]), "agentce:BudgetUnjudged"),
        )
        for e in events
    }


def test_task_budget_tokens() -> None:
    """CND-07 (18.175): a task's ModelCall tokens are summed against its tokens budget; every action of the task
    reads over budget once the sum exceeds it, and an unreadable usage leaves an under-limit task untold."""
    under = [
        _model_call("m1", "review", {"input_tokens": 400, "output_tokens": 100}),
        _scoped_call("t1", "lookup", "read", agentcetask="review"),
    ]
    assert _budget_flags(under, {"tokens": 500}) == {
        "m1": ("true", True),
        "t1": ("true", True),
    }
    over = [
        *under,
        _model_call("m2", "review", {"input_tokens": 1, "output_tokens": 0}),
    ]
    assert set(_budget_flags(over, {"tokens": 500}).values()) == {("false", True)}
    unread = [*under, _model_call("m2", "review", {"input_tokens": 1})]
    assert set(_budget_flags(unread, {"tokens": 500}).values()) == {("true", False)}
    # an overrun the readable usage already shows is told, whatever else is unreadable
    assert set(_budget_flags([*over, unread[-1]], {"tokens": 500}).values()) == {
        ("false", True)
    }
    for limit in (500.5, "500", True, -1):
        assert set(_budget_flags(under, {"tokens": limit}).values()) == {
            ("true", False)
        }
    # a whole number written 500.0 reads as 500, as every engine's reader may give it
    assert set(_budget_flags(over, {"tokens": 500.0}).values()) == {("false", True)}


def test_task_budget_tokens_past_2_53() -> None:
    """CND-07 (18.175): token counts are held to 2^53, the largest every engine holds exactly. A usage or a sum
    past 2^53 - 1 saturates and reads over every readable limit; a tokens limit past 2^53 - 1 is unreadable."""
    big = 2**53 - 1
    many = [
        _model_call(f"m{i}", "review", {"input_tokens": big, "output_tokens": big})
        for i in range(513)
    ]
    assert set(_budget_flags(many, {"tokens": 1000}).values()) == {("false", True)}
    huge = [_model_call("m", "review", {"input_tokens": 2**62, "output_tokens": 0})]
    assert set(_budget_flags(huge, {"tokens": big}).values()) == {("false", True)}
    # 2^54 + 1 tokens against a 2^54 limit: the limit cannot be held exactly in every engine, so it is untold
    past = [_model_call("m", "review", {"input_tokens": 2**54 + 1, "output_tokens": 0})]
    assert set(_budget_flags(past, {"tokens": 2**54}).values()) == {("true", False)}
    edge = [_model_call("m", "review", {"input_tokens": big, "output_tokens": 0})]
    assert set(_budget_flags(edge, {"tokens": big}).values()) == {("true", True)}
    assert set(_budget_flags(edge, {"tokens": big - 1}).values()) == {("false", True)}


def test_task_budget_spend_compute_and_scope() -> None:
    """Spend is told only against a zero budget; compute time is never recorded; a task held to no scope cannot be
    told while the binding declares scopes; an untasked call and a budget left out set no limit."""
    spend = [_scoped_call("s1", "pay", "spend", agentcetask="review")]
    assert _budget_flags(spend, {"spend": 0}) == {"s1": ("false", True)}
    assert _budget_flags(spend, {"spend": 10}) == {"s1": ("true", False)}
    read = [_scoped_call("r1", "lookup", "read", agentcetask="review")]
    assert _budget_flags(read, {"spend": 10}) == {"r1": ("true", True)}
    # a call with no effect class may have spent, and a task with no model call shows no token use (critic r1 B1, B2)
    unknown = [_scoped_call("x1", "pay", None, agentcetask="review")]  # type: ignore[arg-type]
    assert _budget_flags(unknown, {"spend": 0}) == {"x1": ("true", False)}
    assert _budget_flags(read, {"tokens": 5}) == {"r1": ("true", False)}
    # a model call may have cost an amount no record carries (critic r2 B5)
    billed = [
        *read,
        _model_call("m1", "review", {"input_tokens": 1, "output_tokens": 1}),
    ]
    assert set(_budget_flags(billed, {"spend": 0}).values()) == {("true", False)}
    assert _budget_flags(read, {"compute_seconds": 60}) == {"r1": ("true", False)}
    assert _budget_flags(read, "lots") == {"r1": ("true", False)}
    assert _budget_flags(read, None) == {"r1": ("true", True)}
    other = [
        _scoped_call("o1", "lookup", "read", agentcetask="elsewhere"),
        _scoped_call("u1", "lookup", "read"),
    ]
    flags = _budget_flags(
        other,
        {"tokens": 5},
        task_scopes=[
            {"task_type": "review", "budgets": {"tokens": 5}},
            {"task_type": "pay"},
        ],
    )
    assert flags == {"o1": ("true", False), "u1": ("true", True)}
    store = build_graph(other, domain=DomainBinding.from_dict({}))
    assert not store.is_a(event_iri("o1"), "agentce:BudgetUnjudged")
    untasked = _model_call("m0", None, {"input_tokens": 9, "output_tokens": 9})
    assert not build_graph([untasked], domain=_SCOPES).is_a(
        event_iri("m0"), "agentce:TaskModelCall"
    )


# --- CND-02: actions are the population, and each one's gate (SPEC §7.7.4; 18.187) ---------------------------------

_GATED = DomainBinding.from_dict(
    {
        "decision_types": [
            {
                "id": CREDIT,
                "subclass_of": "agentce:ConsequentialDecision",
                "consequential": True,
            },
            {"id": MINOR, "subclass_of": "agentce:Decision", "consequential": False},
        ],
        "task_scopes": [
            {
                "task_type": "review",
                "allowed_effect_classes": ["read", "write"],
                "requires_approval": ["irreversible", "write"],
            },
            {"task_type": "payout", "allowed_effect_classes": ["spend"]},
        ],
    }
)


def _gate_events(
    effect: str | None = "irreversible",
    policy: str | None = "require_approval",
    *,
    approved: bool = True,
    dtype: str = CREDIT,
    task: str | None = "review",
    decision_ref: Any = "agentce:event/d1",
    policy_class: str = "enforcement_point",
    request: str = "tc1",
    ptype: str = "ToolCall",
) -> list[dict[str, Any]]:
    events = [_event("d1", "Decision", {"decision_type": dtype})]
    events[0]["time"] = "2026-01-01T00:00:00Z"
    if approved:
        events += [_login("login-alice"), _approval("ap1", ALICE)]
        events[-1]["time"] = "2026-01-01T00:00:01Z"
    if policy is not None:
        pd = _event(
            "pd1",
            "PolicyDecision",
            {"decision": policy, "refs": {"request": event_iri(request)}},
        )
        pd["agentcesourceclass"] = policy_class
        events.append(pd)
    data: dict[str, Any] = {
        "tool": {"name": "book"},
        "refs": {"decision": decision_ref},
    }
    if effect is not None:
        data["effect_class"] = effect
    action = _event("tc1", ptype, data)
    action["time"] = "2026-01-01T00:00:02Z"
    if task is not None:
        action["agentcetask"] = task
    return events + [action]


def _gate(
    events: list[dict[str, Any]], node: str = "tc1"
) -> tuple[bool, str | None, str | None]:
    store = build_graph(events, domain=_GATED)
    iri = event_iri(node)
    met = store.literal_values(iri, "agentce:gateMet")
    judged = store.literal_values(iri, "agentce:gateJudged")
    return (
        store.is_a(iri, "agentce:GatedAction"),
        met[0] if met else None,
        judged[0] if judged else None,
    )


def test_cnd02_requires_approval_action_needs_an_enforcement_point_decision_and_an_approval() -> (
    None
):
    assert _gate(_gate_events()) == (True, "true", "true")
    assert _gate(_gate_events(policy="allow")) == (True, "true", "true")
    assert _gate(_gate_events(approved=False)) == (True, "false", "true")
    # the agent's own allow, an allow naming another call, and no PolicyDecision at all are no gate
    assert _gate(_gate_events(policy_class="self_report")) == (True, "false", "true")
    assert _gate(_gate_events(request="tcx")) == (True, "false", "true")
    assert _gate(_gate_events(policy=None)) == (True, "false", "true")


def test_cnd02_a_deny_closes_the_gate_whatever_was_approved() -> None:
    assert _gate(_gate_events(policy="deny")) == (True, "false", "true")
    assert _gate(_gate_events("external_communication", "deny")) == (
        True,
        "false",
        "true",
    )


def test_cnd02_the_approval_is_tied_to_the_decision_the_action_names() -> None:
    for ref in (None, "agentce:event/d-missing", "agentce:event/d2"):
        assert _gate(_gate_events(decision_ref=ref)) == (True, "false", "true")
    events = _gate_events()
    events[-1]["data"]["refs"].pop("decision")
    assert _gate(events) == (True, "false", "true")


def test_cnd02_other_high_effect_classes_need_an_allow_or_a_released_require_approval() -> (
    None
):
    for effect in ("external_communication", "spend", "physical"):
        assert _gate(_gate_events(effect, "allow", approved=False)) == (
            True,
            "true",
            "true",
        )
        assert _gate(_gate_events(effect, "require_approval")) == (True, "true", "true")
        assert _gate(_gate_events(effect, "require_approval", approved=False)) == (
            True,
            "false",
            "true",
        )
        assert _gate(_gate_events(effect, "allow", policy_class="self_report")) == (
            True,
            "false",
            "true",
        )


def test_cnd02_the_scope_marks_the_population_and_read_stays_outside() -> None:
    assert _gate(_gate_events("read", None, approved=False)) == (False, None, None)
    # write is no high-effect class, but this scope marks it requires_approval
    assert _gate(_gate_events("write", "allow", approved=False)) == (
        True,
        "false",
        "true",
    )
    assert _gate(_gate_events("write", "allow")) == (True, "true", "true")
    # the decision's own type does not matter: the action is what CND-02 counts
    assert _gate(_gate_events(dtype=MINOR)) == (True, "true", "true")


def test_cnd02_a_high_effect_action_in_no_declared_scope_is_held_to_the_approval_path() -> (
    None
):
    for task in (None, "unknown-task"):
        assert _gate(
            _gate_events("external_communication", "allow", approved=False, task=task)
        ) == (
            True,
            "false",
            "true",
        )
        assert _gate(_gate_events("write", "allow", approved=False, task=task)) == (
            False,
            None,
            None,
        )


def test_cnd02_an_action_with_no_effect_class_and_an_unexecuted_decision_are_unjudged() -> (
    None
):
    assert _gate(_gate_events(None)) == (True, "true", "false")
    events = _gate_events()[:-1]
    assert _gate(events, "d1") == (True, "true", "false")
    # a minor decision no action executes is not in the population
    assert _gate(_gate_events(dtype=MINOR)[:-1], "d1") == (False, None, None)


def test_cnd02_a_resource_access_counts_only_with_an_effect_class() -> None:
    assert _gate(_gate_events(ptype="ResourceAccess")) == (True, "true", "true")
    assert _gate(_gate_events(None, ptype="ResourceAccess")) == (False, None, None)


def test_cnd02_released_by_links_the_approvals_the_gate_rests_on() -> None:
    def released(events: list[dict[str, Any]]) -> list[str]:
        return build_graph(events, domain=_GATED).objects(
            event_iri("tc1"), "agentce:releasedBy"
        )

    assert released(_gate_events()) == [event_iri("ap1")]
    # an allow-path action passed on the enforcement point's allow rests on no approval
    assert released(_gate_events("external_communication", "allow")) == []
    assert released(_gate_events("external_communication", "require_approval")) == [
        event_iri("ap1")
    ]


def _roles_domain(roles: Any = ("credit_officer",)) -> DomainBinding:
    scope: dict[str, Any] = {"task_type": "review", "allowed_effect_classes": ["read"]}
    if roles is not None:
        scope["authorised_principal_roles"] = list(roles)
    return DomainBinding.from_dict(
        {
            "decision_types": [
                {
                    "id": CREDIT,
                    "subclass_of": "agentce:ConsequentialDecision",
                    "consequential": True,
                },
                {
                    "id": MINOR,
                    "subclass_of": "agentce:Decision",
                    "consequential": False,
                },
            ],
            "task_scopes": [scope],
        }
    )


def _provenance_events(
    *,
    instruction: Any = "agentce:event/i1",
    source_class: Any = "user",
    principal: Any = {"id": "user:alice", "role": "credit_officer"},
    parent: dict[str, Any] | None = None,
    origin: dict[str, Any] | None = None,
    delegation: str | None = "verified",
    dtype: str = CREDIT,
) -> list[dict[str, Any]]:
    """A consequential decision d1 executed by tc1, both acting on i1 (CND-04, SPEC §7.7.4)."""
    data: dict[str, Any] = {"source_class": source_class}
    if principal is not None:
        data["principal"] = principal
    events = [
        _event(
            "d1",
            "Decision",
            {"decision_type": dtype, "refs": {"instruction": instruction}},
        ),
        _event("i1", "Instruction", data),
    ]
    refs: dict[str, Any] = {"decision": "agentce:event/d1", "instruction": instruction}
    if delegation is not None:
        refs["delegation"] = "agentce:event/g1"
        if delegation != "unheld":
            events.append(
                _event(
                    "g1", "DelegationIssued", {"verification": {"status": delegation}}
                )
            )
    events.append(_event("tc1", "ToolCall", {"tool": {"name": "book"}, "refs": refs}))
    for key, event in (("parent", parent), ("origin", origin)):
        if event is not None:
            events.append(event)
            events[1]["data"].setdefault("refs", {})[key] = event_iri(event["id"])
    return events


def _provenance(
    events: list[dict[str, Any]], node: str = "tc1", domain: DomainBinding | None = None
) -> tuple[bool, str | None, str | None]:
    store = build_graph(events, domain=domain or _roles_domain())
    iri = event_iri(node)
    met = store.literal_values(iri, "agentce:provenanceMet")
    judged = store.literal_values(iri, "agentce:provenanceJudged")
    return (
        store.is_a(iri, "agentce:ConsequentialAction"),
        met[0] if met else None,
        judged[0] if judged else None,
    )


def test_cnd04_population_is_consequential_actions() -> None:
    events = _provenance_events()
    assert _provenance(events) == (True, "true", "true")
    assert _provenance(events, "d1") == (True, "true", "true")
    # a call executing a minor decision, and the minor decision, are outside CND-04
    minor = _provenance_events(dtype=MINOR)
    assert _provenance(minor) == (False, None, None)
    assert _provenance(minor, "d1") == (False, None, None)


def test_cnd04_chain_must_end_at_an_authorised_principal() -> None:
    for cls in ("user", "operator", "service", "agent_identified"):
        assert _provenance(_provenance_events(source_class=cls)) == (
            True,
            "true",
            "true",
        )
    bad_classes: tuple[str | None, ...] = (
        "tool_output",
        "retrieved",
        "memory_untrusted",
        "memory_trusted",
        "agent_unidentified",
        None,
    )
    for bad in bad_classes:
        assert _provenance(_provenance_events(source_class=bad)) == (
            True,
            "false",
            "true",
        )
    for principal in (None, {}, {"id": ""}, "user:alice"):
        assert _provenance(_provenance_events(principal=principal)) == (
            True,
            "false",
            "true",
        )
    # every instruction on the parent chain counts, not only the acted-on one
    bad_parent = _event("i0", "Instruction", {"source_class": "tool_output"})
    assert _provenance(_provenance_events(parent=bad_parent)) == (True, "false", "true")
    # the role is read at the chain's end, the parent here
    good_parent = _event(
        "i0",
        "Instruction",
        {
            "source_class": "operator",
            "principal": {"id": "op:1", "role": "credit_officer"},
        },
    )
    assert _provenance(_provenance_events(parent=good_parent)) == (True, "true", "true")
    # three hops: a user parent whose own parent is tool_output still fails
    root = _event("i9", "Instruction", {"source_class": "tool_output"})
    middle = _event(
        "i0",
        "Instruction",
        {**good_parent["data"], "refs": {"parent": event_iri("i9")}},
    )
    deep = _provenance_events(parent=middle) + [root]
    assert _provenance(deep) == (True, "false", "true")
    # arrival order does not change the reading
    assert _provenance(deep[::-1]) == (True, "false", "true")
    assert _provenance(_provenance_events(parent=good_parent)[::-1]) == (
        True,
        "true",
        "true",
    )
    # a parent cycle never ends at a principal: unjudged
    cycle = _provenance_events(
        parent=_event("i0", "Instruction", dict(good_parent["data"]))
    )
    cycle[-1]["data"]["refs"] = {"parent": event_iri("i1")}
    assert _provenance(cycle) == (True, "true", "false")
    assert _provenance(
        _provenance_events(principal={"id": "  ", "role": "credit_officer"})
    ) == (True, "false", "true")


def test_cnd04_principal_role_must_be_authorised_for_the_task_type() -> None:
    events = _provenance_events()
    assert _provenance(events, domain=_roles_domain(["claims_supervisor"])) == (
        True,
        "false",
        "true",
    )
    # no roles declared, no role on the principal, or no scope for the task: the records cannot show it
    assert _provenance(events, domain=_roles_domain(None)) == (True, "true", "false")
    assert _provenance(_provenance_events(principal={"id": "user:alice"})) == (
        True,
        "true",
        "false",
    )
    assert _provenance(events, domain=_GATED) == (True, "true", "false")
    # an empty list authorises no role
    assert _provenance(events, domain=_roles_domain([])) == (True, "false", "true")
    # the Decision takes the task of the call executing it
    tasked = _provenance_events()
    tasked[-1]["agentcetask"] = "review"
    assert _provenance(tasked, "d1", _roles_domain(["claims_supervisor"])) == (
        True,
        "false",
        "true",
    )


def test_cnd04_an_instruction_that_is_not_held_fails() -> None:
    for ref in (None, "agentce:event/i-missing", "i1", "agentce:event/g1"):
        assert _provenance(_provenance_events(instruction=ref)) == (
            True,
            "false",
            "true",
        )


def test_cnd04_origin_from_a_tool_call_or_untrusted_read_fails() -> None:
    call = _event("o1", "ToolCall", {"tool": {"name": "fetch"}})
    assert _provenance(_provenance_events(origin=call)) == (True, "false", "true")
    read = _event("o1", "MemoryRead", {"trust_min": "untrusted"})
    assert _provenance(_provenance_events(origin=read)) == (True, "false", "true")
    trusted = _event("o1", "MemoryRead", {"trust_min": "trusted"})
    assert _provenance(_provenance_events(origin=trusted)) == (True, "true", "true")
    for trust in (None, "", "unknown", "Untrusted", ["untrusted"]):
        unread = _event(
            "o1", "MemoryRead", {} if trust is None else {"trust_min": trust}
        )
        assert _provenance(_provenance_events(origin=unread)) == (True, "true", "false")
    access = _event("o1", "ResourceAccess", {})
    assert _provenance(_provenance_events(origin=access)) == (True, "false", "true")
    # an origin of a type §6.2.3 does not allow there cannot be judged
    other = _event("o1", "SessionStart", {})
    assert _provenance(_provenance_events(origin=other)) == (True, "true", "false")


def _self_reported(
    events: list[dict[str, Any]], *witnesses: dict[str, Any]
) -> list[dict[str, Any]]:
    """``events`` with i1 recorded by the agent (self_report) and the enforcement-point witnesses appended."""
    events[1]["agentcesourceclass"] = "self_report"
    return events + list(witnesses)


def _pd_allow(principal: Any, request: str = "agentce:event/tc1") -> dict[str, Any]:
    return _event(
        "pd1",
        "PolicyDecision",
        {"decision": "allow", "principal": principal, "refs": {"request": request}},
    )


def test_cnd04_a_self_reported_role_must_match_the_enforcement_point_record() -> None:
    alice = {"id": "user:alice", "role": "credit_officer"}
    for node in ("tc1", "d1"):
        # the policy decision allowing the call records the same role: read as recorded
        events = _self_reported(_provenance_events(), _pd_allow(alice))
        assert _provenance(events, node) == (True, "true", "true")
        # another role for the same principal: the agent's word is contradicted
        other = {"id": "user:alice", "role": "loan_manager"}
        events = _self_reported(_provenance_events(), _pd_allow(other))
        assert _provenance(events, node) == (True, "false", "true")
        # no role on the witness: the records cannot show the role
        events = _self_reported(_provenance_events(), _pd_allow({"id": "user:alice"}))
        assert _provenance(events, node) == (True, "true", "false")
    # an enforcement-point twin of the instruction (same content_ref, task and class) recording another role
    same = {"content_ref": "sha256:aa", "refs": {"task": "task-1"}}
    twin = _event(
        "i1-ep",
        "Instruction",
        {
            "source_class": "user",
            "principal": {"id": "user:alice", "role": "loan_manager"},
            **same,
        },
    )
    events = _provenance_events()
    events[1]["data"].update(same)
    assert _provenance(_self_reported(events, twin)) == (True, "false", "true")
    # an instruction with no content_ref has no twin
    assert _provenance(_self_reported(_provenance_events(), twin)) == (
        True,
        "true",
        "true",
    )
    # a witness for another principal, or allowing another request, is no witness of this role
    mallory = {"id": "user:mallory", "role": "loan_manager"}
    events = _self_reported(_provenance_events(), _pd_allow(mallory))
    assert _provenance(events) == (True, "true", "true")
    events = _self_reported(
        _provenance_events(),
        _pd_allow({"id": "user:alice"}, "agentce:event/tc9"),
    )
    assert _provenance(events) == (True, "true", "true")
    # an enforcement-point instruction is read as recorded, whatever a policy decision says
    events = _provenance_events() + [_pd_allow({"id": "user:alice"})]
    assert _provenance(events) == (True, "true", "true")


def test_cnd04_a_definite_failure_beats_an_unjudged_role() -> None:
    events = _provenance_events(delegation="unverified")
    assert _provenance(events, domain=_roles_domain(None)) == (True, "false", "true")
    assert _provenance(events, domain=_GATED) == (True, "false", "true")


def test_cnd04_reads_authorised_principal_roles_strictly() -> None:
    events = _provenance_events()
    # padding is trimmed on both sides; the comparison is case-sensitive
    assert _provenance(events, domain=_roles_domain([" credit_officer "])) == (
        True,
        "true",
        "true",
    )
    padded = _provenance_events(
        principal={"id": "user:alice", "role": " credit_officer"}
    )
    assert _provenance(padded) == (True, "true", "true")
    assert _provenance(events, domain=_roles_domain(["Credit_Officer"])) == (
        True,
        "false",
        "true",
    )
    # a bare string, null or a list with a non-string member is unreadable, never a shorter list
    for roles in (
        "credit_officer",
        None,
        ["credit_officer", 7],
        ["loan_manager", None],
    ):
        scope: dict[str, Any] = {
            "task_type": "review",
            "allowed_effect_classes": ["read"],
            "authorised_principal_roles": roles,
        }
        domain = DomainBinding.from_dict(
            {
                "decision_types": [
                    {
                        "id": CREDIT,
                        "subclass_of": "agentce:ConsequentialDecision",
                        "consequential": True,
                    }
                ],
                "task_scopes": [scope],
            }
        )
        assert _provenance(events, domain=domain) == (True, "true", "false"), roles


def test_cnd04_reads_the_delegation_chain() -> None:
    assert _provenance(_provenance_events(delegation="unverified")) == (
        True,
        "false",
        "true",
    )
    assert _provenance(_provenance_events(delegation="unverified"), "d1") == (
        True,
        "false",
        "true",
    )
    # no delegation, or one the bundle does not hold: the records cannot judge it (fail closed)
    assert _provenance(_provenance_events(delegation=None)) == (True, "true", "false")
    assert _provenance(_provenance_events(delegation="unheld")) == (
        True,
        "true",
        "false",
    )
