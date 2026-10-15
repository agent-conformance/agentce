"""A control's min_source_class, enforced per assertion (SPEC §6.4, HR-5, §7.7.5; item 18.127).

The judged class of a focus, the attestation of an instruction it acts on (each of conditions (i)-(iv) and both
corroboration branches, with hostile twins that differ from an attested one by one field), the refusal of an
unknown minimum at catalog load and lint, and the downgrade, which only ever turns a conformant verdict into
insufficient_evidence.
"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any

import pytest

from agentce.assess import assess_subjects, missing_evidence
from agentce.blind_spots import compute_blind_spots
from agentce.catalog import Catalog, lint_catalog, load_catalog
from agentce.domain import DomainBinding
from agentce.errors import InputError
from agentce.graph import build_graph
from agentce.iri import event_iri
from agentce.profile import Profile
from agentce.source_class import (
    BELOW_MINIMUM_OUTCOME,
    SOURCE_CLASS_RANK,
    SourceClassJudge,
    class_rank,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CATALOGS = _REPO_ROOT / "spec" / "catalogs"
SUBJECT = "spiffe://corp/agents/source-class"
OTHER = "spiffe://corp/agents/other"
EP, SR, IS = "enforcement_point", "self_report", "independent_system"
ALICE = {"id": "user:alice", "kind": "human"}
MALLORY = {"id": "user:mallory", "kind": "human"}
CONTENT = "sha256:" + "a" * 64
DOMAIN_TYPES = [
    {
        "id": "dom:CreditDecision",
        "subclass_of": "agentce:ConsequentialDecision",
        "consequential": True,
    }
]
DOMAIN = DomainBinding.from_dict({"decision_types": DOMAIN_TYPES})


def _event(
    eid: str, ptype: str, cls: str, subject: str = SUBJECT, **data: Any
) -> dict[str, Any]:
    return {
        "id": eid,
        "source": f"urn:src:{cls}",
        "subject": subject,
        "type": f"org.agent-conformance.evidence.{ptype}.v1",
        "agentcesourceclass": cls,
        "time": f"2026-01-02T00:00:{len(eid):02d}Z",
        "specversion": "1.0",
        "data": {"@type": ptype, **{k: v for k, v in data.items() if v is not None}},
    }


def _instruction(
    eid: str = "i1",
    cls: str = EP,
    source_class: str | None = "user",
    principal: dict[str, str] | None = ALICE,
    content: str = CONTENT,
    subject: str = SUBJECT,
    **refs: str,
) -> dict[str, Any]:
    return _event(
        eid,
        "Instruction",
        cls,
        subject,
        source_class=source_class,
        principal=principal,
        content_ref=content,
        refs={"task": "task-1", **refs},
    )


def _call(cls: str = EP, instruction: str = "i1", **refs: str) -> dict[str, Any]:
    return _event(
        "tc1",
        "ToolCall",
        cls,
        agent={"id": SUBJECT},
        tool={"name": "lookup", "protocol": "native"},
        effect_class="read",
        refs={"instruction": event_iri(instruction), **refs},
    )


def _policy(
    cls: str = EP,
    decision: str = "allow",
    principal: dict[str, str] | None = ALICE,
    request: str = "tc1",
) -> dict[str, Any]:
    return _event(
        "pd1",
        "PolicyDecision",
        cls,
        engine="custom",
        policy_id="ingress",
        policy_version="1",
        decision=decision,
        principal=principal,
        refs={"request": event_iri(request)},
    )


def _read(trust_min: str | None = "trusted", cls: str = EP) -> dict[str, Any]:
    return _event(
        "m1", "MemoryRead", cls, store="kb", record_refs=["mem:r1"], trust_min=trust_min
    )


def _judge(events: list[dict[str, Any]]) -> SourceClassJudge:
    own = [e for e in events if e["subject"] == SUBJECT]
    store = build_graph(own, domain=DOMAIN)
    return SourceClassJudge(store, {event_iri(e["id"]): e for e in own})


def _attested(events: list[dict[str, Any]], focus: str = "tc1") -> bool:
    return _judge(events).attested(event_iri(focus), event_iri("i1"))


# --- the class order and judged_class --------------------------------------------------------------------------


def test_the_class_order_is_the_spec_order_and_anything_else_ranks_lowest() -> None:
    assert sorted(SOURCE_CLASS_RANK, key=SOURCE_CLASS_RANK.__getitem__) == [SR, EP, IS]
    for odd in ("any", "Enforcement_Point", "", None, 1):
        assert class_rank(odd) == 0


def test_judged_class_is_the_lowest_of_the_focus_and_what_it_acts_on() -> None:
    focus = event_iri("tc1")
    judged = _judge([_instruction(cls=SR), _call()]).judged_class(focus, ["actsOn"])
    assert judged.rank == 0 and judged.lowest == ("Instruction",)
    judged = _judge([_instruction(), _call()]).judged_class(focus, ["actsOn"])
    assert judged.rank == 1
    judged = _judge([_instruction(), _call(cls=SR)]).judged_class(focus, ["actsOn"])
    assert judged.rank == 0 and judged.lowest == ("ToolCall",)
    # Without source_class_from only the focus's own class counts.
    judged = _judge([_instruction(cls=SR), _call()]).judged_class(focus, [])
    assert judged.rank == 1


def test_a_decision_counts_only_what_it_rests_on() -> None:
    decision = _event(
        "d1",
        "Decision",
        IS,
        decision_type="dom:CreditDecision",
        refs={"instruction": event_iri("i1")},
    )
    judge = _judge([_instruction(cls=SR), decision])
    judged = judge.judged_class(event_iri("d1"), ["actsOn"])
    assert judged.rank == 0  # its own independent_system class is not counted
    judged = _judge([_instruction(), decision]).judged_class(
        event_iri("d1"), ["actsOn"]
    )
    assert judged.rank == 1


def test_a_decision_that_reaches_nothing_is_judged_through_its_calls_or_as_self_report() -> (
    None
):
    """A Decision naming no instruction rests on what the calls executing it act on; with none, on its own
    self_report record, so leaving its link out never reads better than naming a self_report instruction."""
    lone = _event("d2", "Decision", SR, decision_type="dom:CreditDecision")
    judged = _judge([lone]).judged_class(event_iri("d2"), ["actsOn"])
    assert (judged.rank, judged.lowest) == (0, ("Decision",))
    executes = _call(decision=event_iri("d2"))
    judged = _judge([_instruction(), lone, executes]).judged_class(
        event_iri("d2"), ["actsOn"]
    )
    assert judged.rank == 1
    judged = _judge([_instruction(cls=SR), lone, executes]).judged_class(
        event_iri("d2"), ["actsOn"]
    )
    assert judged.rank == 0
    bare = _call(decision=event_iri("d2"))
    del bare["data"]["refs"]["instruction"]
    judged = _judge([lone, bare]).judged_class(event_iri("d2"), ["actsOn"])
    assert (judged.rank, judged.lowest) == (0, ("Decision",))


def test_an_unattested_independent_system_instruction_counts_as_self_report() -> None:
    judged = _judge([_instruction(cls=IS), _call()]).judged_class(
        event_iri("tc1"), ["actsOn"]
    )
    assert judged.rank == 0


# --- attestation (i)-(iv) --------------------------------------------------------------------------------------


def test_i_an_instruction_is_attested_only_when_held_by_its_graph_iri() -> None:
    assert _attested([_instruction(), _call()])
    bare = _call()
    bare["data"]["refs"]["instruction"] = "i1"
    judge = _judge([_instruction(), bare])
    assert not judge.attested(event_iri("tc1"), "i1")
    assert not _attested([_instruction(subject=OTHER), _call()])


def test_ii_every_parent_must_be_held_and_attested() -> None:
    parent = _instruction("i0", cls=EP, content="sha256:" + "0" * 64)
    child = _instruction(parent=event_iri("i0"))
    assert _attested([parent, child, _call()])
    sr_parent = _instruction("i0", cls=SR, content="sha256:" + "0" * 64)
    assert not _attested([sr_parent, child, _call()])
    assert not _attested([child, _call()])  # the parent is not held
    cycle = _instruction("i0", cls=EP, parent=event_iri("i1"))
    assert _attested([cycle, child, _call()])


@pytest.mark.parametrize(
    ("read", "attested"),
    [
        (_read("trusted"), True),
        (_read("untrusted"), False),
        (_read(None), False),
        (_read("trusted", cls=SR), False),
    ],
    ids=["ep-trusted", "ep-untrusted", "ep-no-trust-min", "self-report-read"],
)
def test_iii_an_origin_must_be_an_enforcement_point_read_at_trust_min_trusted(
    read: dict[str, Any], attested: bool
) -> None:
    instruction = _instruction(source_class="memory_trusted", origin=event_iri("m1"))
    assert _attested([read, instruction, _call()]) is attested
    gone = _instruction(source_class="memory_trusted", origin=event_iri("gone"))
    assert not _attested([gone, _call()])


def test_iv_a_self_report_instruction_is_not_attested_on_its_own() -> None:
    assert not _attested([_instruction(cls=SR), _call()])


def _twin(**over: Any) -> dict[str, Any]:
    twin = _instruction("i1-ingress", cls=EP)
    twin["time"] = "2026-01-02T00:00:00Z"  # at ingress, before the call
    for key, value in over.items():
        if key in ("task", "parent", "origin"):
            twin["data"]["refs"][key] = value
        elif key == "subject":
            twin["subject"] = value
        elif key == "cls":
            twin["agentcesourceclass"] = value
        elif key == "time":
            twin["time"] = value
        else:
            twin["data"][key] = value
    return twin


def test_iv_an_attested_enforcement_point_twin_witnesses_the_class() -> None:
    assert _attested([_instruction(cls=SR), _twin(), _call()])
    nobody = _instruction(cls=SR, principal=None, source_class="memory_trusted")
    twin = _twin(principal=None, source_class="memory_trusted")
    del twin["data"]["principal"]
    assert _attested([nobody, twin, _call()])


def test_iv_an_enforcement_point_policy_decision_for_the_focus_witnesses_the_class() -> (
    None
):
    assert _attested([_instruction(cls=SR), _call(), _policy()])


@pytest.mark.parametrize(
    "hostile",
    [
        [_twin(content_ref="sha256:" + "b" * 64)],
        [_twin(task="task-2")],
        [_twin(subject=OTHER)],
        [_twin(source_class="operator")],
        [_twin(principal=MALLORY)],
        [_twin(parent=event_iri("i0")), _instruction("i0", cls=SR, content="x")],
        [
            _twin(parent=event_iri("i0")),
            _instruction("i0", cls=EP, source_class="tool_output", content="x"),
        ],
        [_twin(origin=event_iri("m1")), _read("untrusted")],
        [_policy(request="tc9")],
        [_policy(cls=SR)],
        [_policy(cls=IS)],
        [_policy(decision="deny")],
        [_policy(principal=MALLORY)],
        [_policy(principal=None)],
        [_twin(cls=SR)],
    ],
    ids=[
        "twin-other-content",
        "twin-other-task",
        "twin-other-subject",
        "twin-other-authorised-class",
        "twin-other-principal",
        "twin-self-report-parent",
        "twin-tool_output-parent",
        "twin-untrusted-origin",
        "pd-other-request",
        "pd-self-report",
        "pd-independent-system",
        "pd-deny",
        "pd-other-principal",
        "pd-no-principal",
        "twin-self-report",
    ],
)
def test_hostile_corroboration_never_lifts_a_self_report_instruction(
    hostile: list[dict[str, Any]],
) -> None:
    assert not _attested([_instruction(cls=SR), _call(), *copy.deepcopy(hostile)])


def test_a_policy_decision_does_not_lift_a_self_report_parent() -> None:
    parent = _instruction("i0", cls=SR, content="sha256:" + "0" * 64)
    child = _instruction(cls=SR, parent=event_iri("i0"))
    assert not _attested([parent, child, _call(), _policy()])
    # Nor does it lift a self-report parent below an enforcement-point child.
    ep_child = _instruction(cls=EP, parent=event_iri("i0"))
    assert not _attested([parent, ep_child, _call(), _policy()])


def test_a_corroborated_instruction_still_needs_its_origin() -> None:
    instruction = _instruction(cls=SR, origin=event_iri("m1"))
    assert not _attested([_read("untrusted"), instruction, _call(), _policy()])
    assert _attested([_read("trusted"), instruction, _call(), _policy()])


# --- the refusal of an unknown minimum ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "shown"),
    [
        ("min_source_class: independent", '"independent"'),
        (None, "(missing)"),
        ("min_source_class: null", "null"),
        ('min_source_class: ""', '""'),
        ("min_source_class: 1", "1"),
        ("min_source_class: Enforcement_Point", '"Enforcement_Point"'),
        ("min_source_class: any", '"any"'),
    ],
    ids=["independent", "missing", "null", "empty", "number", "case", "any"],
)
def test_an_unknown_minimum_is_refused_at_load_and_lint(
    tmp_path: Path, line: str | None, shown: str
) -> None:
    catalog_dir = tmp_path / "conduct"
    shutil.copytree(_CATALOGS / "overlays" / "conduct", catalog_dir)
    control = catalog_dir / "controls" / "CND-05.yaml"
    text = control.read_text(encoding="utf-8")
    replacement = "" if line is None else f"  {line}\n"
    control.write_text(
        text.replace("  min_source_class: enforcement_point\n", replacement),
        encoding="utf-8",
    )
    with pytest.raises(InputError) as excinfo:
        load_catalog(catalog_dir)
    key = "catalog.control.min_source_class_unknown"
    assert excinfo.value.key == key
    assert int(excinfo.value.exit_code) == 3
    assert "CND-05" in excinfo.value.cause and shown in excinfo.value.cause
    assert [p.split(":")[0] for p in lint_catalog(catalog_dir)] == [key]


@pytest.mark.parametrize(
    ("line", "shown"),
    [
        ("source_class_from: [actson]", '["actson"]'),
        ("source_class_from: actsOn", '"actsOn"'),
        ("source_class_from: [parent]", '["parent"]'),
        ("source_class_from: null", "null"),
        ("source_class_from: [actsOn, 1]", '["actsOn", 1]'),
    ],
    ids=["case", "string", "unknown", "null", "number"],
)
def test_an_unknown_source_class_from_is_refused_at_load(
    tmp_path: Path, line: str, shown: str
) -> None:
    catalog_dir = tmp_path / "conduct"
    shutil.copytree(_CATALOGS / "overlays" / "conduct", catalog_dir)
    control = catalog_dir / "controls" / "CND-05.yaml"
    text = control.read_text(encoding="utf-8")
    control.write_text(
        text.replace("  source_class_from: [actsOn]\n", f"  {line}\n"),
        encoding="utf-8",
    )
    with pytest.raises(InputError) as excinfo:
        load_catalog(catalog_dir)
    assert excinfo.value.key == "catalog.control.source_class_from_unknown"
    assert int(excinfo.value.exit_code) == 3
    assert "CND-05" in excinfo.value.cause and shown in excinfo.value.cause


def test_every_bundled_and_spec_catalog_declares_a_known_minimum() -> None:
    for folder in sorted(_CATALOGS.glob("*/*/catalog.yaml")) + sorted(
        _CATALOGS.glob("*/catalog.yaml")
    ):
        for control in load_catalog(folder.parent).controls:
            assert control.min_source_class in SOURCE_CLASS_RANK


# --- the downgrade, through assess --------------------------------------------------------------------------------


def _conduct() -> Catalog:
    return load_catalog(_CATALOGS / "overlays" / "conduct")


def _assess(
    events: list[dict[str, Any]],
    control: str = "CND-05",
    domain: DomainBinding = DOMAIN,
) -> Any:
    profile = Profile.from_dict(
        {"subjects": [{"id": SUBJECT, "role": "both"}, {"id": OTHER, "role": "both"}]}
    )
    assertions = assess_subjects(events, profile, [_conduct()], domain, withheld=())
    return next(a for a in assertions if a.control == control and a.subject == SUBJECT)


def test_a_self_report_instruction_reads_insufficient_evidence_naming_what_is_missing() -> (
    None
):
    assertion = _assess([_instruction(cls=SR), _call()])
    assert assertion.outcome == BELOW_MINIMUM_OUTCOME == "insufficient_evidence"
    assert assertion.source_class_satisfied is False
    assert [e.ref for e in assertion.evidence] == [event_iri("tc1")]
    assert assertion.class_gap == ("Instruction",)
    assert "source_class_satisfied" in assertion.to_json()
    control = next(c for c in _conduct().controls if c.id == "CND-05")
    assert missing_evidence(control, [_instruction(cls=SR), _call()], assertion) == [
        {"event": "Instruction", "class": "enforcement_point"}
    ]


def test_an_attested_instruction_stays_conformant() -> None:
    for events in (
        [_instruction(), _call()],
        [_instruction(cls=SR), _twin(), _call()],
        [_instruction(cls=SR), _call(), _policy()],
    ):
        assertion = _assess(events)
        assert assertion.outcome == "conformant"
        assert assertion.source_class_satisfied is True


def test_the_other_subjects_twin_never_lifts_this_subjects_instruction() -> None:
    assertion = _assess([_instruction(cls=SR), _twin(subject=OTHER), _call()])
    assert assertion.outcome == "insufficient_evidence"


def test_the_downgrade_never_touches_a_non_conformant_verdict() -> None:
    assertion = _assess([_instruction(cls=SR, source_class="tool_output"), _call()])
    assert assertion.outcome == "non-conformant"
    assert assertion.source_class_satisfied is True
    assert assertion.class_gap == ()


def test_a_decision_acting_on_nothing_is_not_held_to_a_class() -> None:
    decision = _event("d1", "Decision", SR, decision_type="dom:CreditDecision")
    call = _call()
    call["data"]["refs"]["decision"] = event_iri("d1")
    assertion = _assess([_instruction(), call, decision])
    assert assertion.outcome == "conformant"


def test_cnd04_reads_the_delegation_it_rests_on() -> None:
    # CND-04 reads the chain's last principal's role against the task type's scope (SPEC §7.7.4, inbox 355).
    roles = DomainBinding.from_dict(
        {
            "decision_types": DOMAIN_TYPES,
            "task_scopes": [
                {
                    "task_type": "review",
                    "authorised_principal_roles": ["credit_officer"],
                }
            ],
        }
    )
    officer = {**ALICE, "role": "credit_officer"}
    decision = _event(
        "d1",
        "Decision",
        SR,
        decision_type="dom:CreditDecision",
        refs={"instruction": event_iri("i1")},
    )

    def delegation(cls: str, status: str = "verified") -> dict[str, Any]:
        return _event(
            "dl1",
            "DelegationIssued",
            cls,
            token_ref="jti-1",
            issuer="https://idp.example",
            chain=[
                {"id": "user:alice", "kind": "human"},
                {"id": SUBJECT, "kind": "agent"},
            ],
            verification={"status": status, "method": "rfc8693"},
        )

    call = _call(decision=event_iri("d1"))
    # No delegation the action rests on: the records cannot show the chain verified (ruling 14 B).
    assert (
        _assess(
            [_instruction(principal=officer), call, decision], "CND-04", roles
        ).outcome
        == "insufficient_evidence"
    )
    call = _call(decision=event_iri("d1"), delegation=event_iri("dl1"))
    # A delegation the records do not hold.
    assert (
        _assess(
            [_instruction(principal=officer), call, decision], "CND-04", roles
        ).outcome
        == "insufficient_evidence"
    )
    downgraded = _assess(
        [_instruction(principal=officer), call, decision, delegation(SR)],
        "CND-04",
        roles,
    )
    assert downgraded.outcome == "insufficient_evidence"
    assert downgraded.class_gap == ("DelegationIssued",)
    events = [_instruction(principal=officer), call, decision, delegation(EP)]
    assert _assess(events, "CND-04", roles).outcome == "conformant"
    failed = _assess([*events[:3], delegation(EP, "failed")], "CND-04", roles)
    assert failed.outcome == "non-conformant"


def test_a_blind_spot_names_the_record_below_the_minimum() -> None:
    profile = Profile.from_dict({"subjects": [{"id": SUBJECT, "role": "both"}]})
    events = [_instruction(cls=SR), _call()]
    catalogs = [_conduct()]
    assertions = assess_subjects(events, profile, catalogs, DOMAIN, withheld=())
    spots = compute_blind_spots(assertions, profile, catalogs, events)
    spot = next(s for s in spots["blind_spots"] if s["event"] == "Instruction")
    assert spot["class"] == "enforcement_point"
    assert spot["checks_unlocked"] == 0 and spot["needed_by"] >= 1


# --- ROB-02 ----------------------------------------------------------------------------------------------------------


def _rob02(events: list[dict[str, Any]]) -> Any:
    profile = Profile.from_dict({"subjects": [{"id": SUBJECT, "role": "both"}]})
    catalog = load_catalog(_CATALOGS / "base" / "baseline")
    assertions = assess_subjects(events, profile, [catalog], DOMAIN, withheld=())
    return next(a for a in assertions if a.control == "ROB-02")


def _memory() -> list[dict[str, Any]]:
    return [
        _event(
            "w0",
            "MemoryWrite",
            EP,
            store="kb",
            record_ref="mem:r0",
            trust="trusted",
            guard_verdict="allow",
        ),
        _event(
            "g0",
            "MemoryRead",
            EP,
            store="kb",
            record_refs=["mem:r0"],
            trust_min="trusted",
        ),
    ]


def test_rob02_an_instruction_without_a_source_class_taints_the_decision() -> None:
    decision = _event(
        "d1",
        "Decision",
        SR,
        decision_type="dom:CreditDecision",
        inputs=["mem:r0"],
        refs={"instruction": event_iri("i1")},
    )
    assert _rob02(
        [*_memory(), _instruction(source_class="user"), decision]
    ).outcome == ("conformant")
    missing = _rob02([*_memory(), _instruction(source_class=None), decision])
    assert missing.outcome == "non-conformant"


def test_rob02_a_decision_that_used_nothing_recorded_reads_insufficient_evidence() -> (
    None
):
    used = _event(
        "d1", "Decision", SR, decision_type="dom:CreditDecision", inputs=["mem:r0"]
    )
    assert _rob02([*_memory(), used]).outcome == "conformant"
    nothing = _event("d1", "Decision", SR, decision_type="dom:CreditDecision")
    assert _rob02([*_memory(), nothing]).outcome == "insufficient_evidence"


def test_iv_a_witness_recorded_after_the_call_attests_nothing() -> None:
    # Ingress records the instruction at entry (SPEC §7.7.5): a twin or allow after the call cannot have gated it.
    late = "2026-01-02T00:00:04Z"
    assert not _attested([_instruction(cls=SR), _twin(time=late), _call()])
    policy = _policy()
    policy["time"] = late
    assert not _attested([_instruction(cls=SR), _call(), policy])
    # The same instant witnesses, whatever offset it is written with.
    assert _attested(
        [_instruction(cls=SR), _twin(time="2026-01-02T05:00:03+05:00"), _call()]
    )


def test_iv_a_witness_has_no_clock_on_a_self_report_call_or_a_missing_time() -> None:
    # A self_report call's time is the agent's own account, so nothing orders a witness before it.
    assert not _attested([_instruction(cls=SR), _twin(), _call(cls=SR)])
    assert not _attested([_instruction(cls=SR), _twin(time=None), _call()])
    assert not _attested([_instruction(cls=SR), _twin(time="soon"), _call()])


def test_iv_a_twin_of_a_user_instruction_needs_a_principal_on_both_sides() -> None:
    blanks: list[Any] = [None, {"id": ""}, {}, "user:alice"]
    for blank in blanks:
        assert not _attested(
            [_instruction(cls=SR, principal=blank), _twin(principal=blank), _call()]
        )


# --- CND-02: the minimum governs the approvals that release an action's gate (SPEC §7.7.4; 18.187) ------------------


def _cnd02_events(
    approvals: tuple[str, ...] = (IS,), call: str = EP, policy: str = "require_approval"
) -> list[dict[str, Any]]:
    events = [_event("d1", "Decision", SR, decision_type="dom:CreditDecision")]
    for n, cls in enumerate(approvals, 1):
        human = {"id": f"user:approver-{n}", "kind": "human"}
        login = _event(
            f"login{n}",
            "SessionStart",
            IS,
            agent={"id": "urn:example:service:identity-provider"},
            environment="idp-login",
            principal=human,
        )
        approval = _event(
            f"ap{n}",
            "ApprovalDecided",
            cls,
            outcome="approve",
            refs={"decision": event_iri("d1")},
            actor=human,
            session_ref=event_iri(f"login{n}"),
        )
        events += [login, approval]
    events.append(_policy(decision=policy, principal={"id": SUBJECT, "kind": "agent"}))
    events.append(
        _event(
            "tc1",
            "ToolCall",
            call,
            agent={"id": SUBJECT},
            tool={"name": "book", "protocol": "native"},
            effect_class="irreversible",
            refs={"decision": event_iri("d1"), "authorization": event_iri("pd1")},
        )
    )
    for n, event in enumerate(events):
        event["time"] = f"2026-01-02T00:00:{n:02d}Z"
    return events


def test_cnd02_an_action_rests_on_the_best_approval_that_releases_it() -> None:
    focus = event_iri("tc1")
    judged = _judge(_cnd02_events()).judged_class(focus, ["releasedBy"])
    assert (judged.rank, judged.lowest) == (SOURCE_CLASS_RANK[IS], ("ApprovalDecided",))
    judged = _judge(_cnd02_events((EP,))).judged_class(focus, ["releasedBy"])
    assert (judged.rank, judged.lowest) == (SOURCE_CLASS_RANK[EP], ("ApprovalDecided",))
    # one approval meeting the minimum releases the gate beside a weaker one
    judged = _judge(_cnd02_events((EP, IS))).judged_class(focus, ["releasedBy"])
    assert judged.rank == SOURCE_CLASS_RANK[IS]
    # an action only the agent recorded stays self_report, whatever approved it
    judged = _judge(_cnd02_events(call=SR)).judged_class(focus, ["releasedBy"])
    assert (judged.rank, judged.lowest) == (SOURCE_CLASS_RANK[SR], ("ToolCall",))


def test_cnd02_outcomes_through_assess() -> None:
    assert _assess(_cnd02_events(), "CND-02").outcome == "conformant"
    assert _assess(_cnd02_events((EP,)), "CND-02").outcome == "insufficient_evidence"
    assert _assess(_cnd02_events((SR, IS)), "CND-02").outcome == "conformant"
    assert _assess(_cnd02_events(call=SR), "CND-02").outcome == "insufficient_evidence"
    assert _assess(_cnd02_events(()), "CND-02").outcome == "non-conformant"
    assert _assess(_cnd02_events(policy="deny"), "CND-02").outcome == "non-conformant"
