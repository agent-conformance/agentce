"""Write cases.json for VG-HELD-DECISION-EVERYWHERE (18.156). Run from this folder: python make_cases.py.

Every refs.decision read resolves to the held decision it names the way the approval bound does since 18.148 (SPEC
§6.3: the name itself, else trimmed of the human-actor rule's id padding, else agentce:event/<trimmed name>, each
checked against the held Decisions): the executes, resultedIn, overriddenBy, interruptedBy and notifiedBy glue edges,
executesConsequential (CND-04, OVS-03 and the other ToolCall shapes that target a consequential action), OVS-07's
Override effectiveness, INC-01's adverse-outcome link and INC-03's incident-to-decision trace. A ToolCall, Outcome,
Override or Notice naming its decision by bare id or padded reads as the one naming it by IRI; one naming no held
decision (an unheld id) reads as before. The referring record comes before the Decision in file order in some cases,
so the resolution never depends on which record was read first."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from approval_events import (  # noqa: E402
    NC,
    SUBJECT,
    appr,
    case,
    dec,
    ev,
    idp,
    ref,
    write,
)

IRI = ref("d1")
FORMS = {
    "iri": IRI,
    "bareid": "d1",
    "padded": " agentce:event/d1\t",
    "padded-bareid": " d1 ",
}
NA = "not_applicable"


def tool(name, **k):
    """The enforcement point ran a tool for the decision, naming it as `name`."""
    return ev("t0", "ToolCall", tool={"name": "x"}, refs={"decision": name, **k})


def override(name):
    """A human's Override of the decision, naming it as `name`, with a replacement that differs (OVS-07)."""
    actor = {"id": "u2", "kind": "human", "authority_ref": "ref:authority/reviewers"}
    return ev(
        "v1",
        "Override",
        refs={"decision": name},
        actor=actor,
        session_ref=ref("s1"),
        original="approve",
        replacement="reject",
        reason_code="harm",
    )


def notice(name):
    """The affected person's Notice of the decision, naming it as `name` (TRN-01, TRN-03)."""
    return ev(
        "n1",
        "Notice",
        refs={"decision": name},
        notice_type="adverse_action",
        person_ref="ref:person/p1",
        delivered_at="2026-01-01T00:10:00Z",
    )


def adverse(name):
    """An adverse Outcome of the decision, naming it as `name` (INC-01)."""
    return ev("o1", "Outcome", refs={"decision": name}, adverse=True)


# CND-04 (Conduct): a ToolCall that executes a consequential decision, and the consequential decision itself, must act
# on a held instruction from an authorised principal through a verified delegation (SPEC §7.7.4). The decision acts on
# an enforcement point's user instruction whose principal holds the role the domain authorises, and the ToolCall rests
# on a verified delegation but names no instruction, so the ToolCall is in CND-04's scope and fails whenever its
# decision is resolved, while the decision passes through it. With a ref naming no held decision the ToolCall executes
# nothing consequential and is out of scope, and the decision, which no ToolCall then executes, rests on no delegation
# (insufficient_evidence, never conformant).
def instruction(i="i0"):
    """An enforcement point's record of a user instruction from a principal the domain authorises (credit_officer)."""
    principal = {"id": "user:alice", "kind": "human", "role": "credit_officer"}
    return ev(
        i,
        "Instruction",
        cls="enforcement_point",
        source_class="user",
        principal=principal,
    )


def delegation():
    """An enforcement point's record of a verified delegation from the decision's principal to the agent."""
    return ev(
        "g0",
        "DelegationIssued",
        cls="enforcement_point",
        token_ref="jti-1",
        issuer="https://idp.example",
        chain=[{"id": "user:alice", "kind": "human"}, {"id": SUBJECT, "kind": "agent"}],
        subject_principal="user:alice",
        actor_principal=SUBJECT,
        scope_granted=["credit:read"],
        scope_parent=["credit:read", "credit:write"],
        expires="2026-02-01T00:00:00Z",
        verification={"status": "verified", "method": "rfc8693"},
    )


def acted(i="i0"):
    """The consequential decision, acting on instruction `i`."""
    return dec(refs={"instruction": ref(i)})


def held(*events):
    """The instruction and the delegation, then `events`."""
    return [instruction(), delegation(), *events]


def delegated(name, **k):
    """`tool(name)` resting on the verified delegation."""
    return tool(name, delegation=ref("g0"), **k)


IE = "insufficient_evidence"
for form, name in FORMS.items():
    case(
        f"cnd04-consequential-toolcall-{form}",
        "CND-04",
        held(acted(), delegated(name)),
        NC,
        ["t0"],
    )
case(
    "cnd04-bareid-toolcall-before-decision",
    "CND-04",
    held(delegated("d1"), acted()),
    NC,
    ["t0"],
)
case(
    "cnd04-consequential-toolcall-unheld",
    "CND-04",
    held(acted(), delegated("nope")),
    IE,
)
# Over-match guard: a bare id naming a held event that is no Decision executes nothing consequential.
case(
    "cnd04-bareid-names-held-non-decision",
    "CND-04",
    held(acted(), ev("o1", "Outcome", refs={"decision": IRI}), delegated("o1")),
    IE,
)
# Positive twin: the bare-id ToolCall acting on an instruction the bundle holds passes CND-04. The instruction is
# an enforcement point's record, so it meets CND-04's minimum class (SPEC HR-5, §7.7.5; 18.127).
case(
    "cnd04-bareid-toolcall-acts-on-instruction",
    "CND-04",
    [
        acted("r1"),
        instruction("r1"),
        delegation(),
        delegated("d1", instruction=ref("r1")),
    ],
    "conformant",
)


def large():
    """A dual-control decision type (the domain requires dual_control) recorded with no oversight modality."""
    d = dec()
    d["data"]["decision_type"] = "dom:LargeLoanDecision"
    return d


# OVS-03 (executes -> oversightModalityMatchesDeclared): a dual-control decision type recorded with no modality.
for form in ("iri", "bareid"):
    case(
        f"ovs03-modality-mismatch-{form}",
        "OVS-03",
        [large(), tool(FORMS[form])],
        NC,
        ["t0"],
    )

# OVS-07: a human's Override is effective only when it names a held decision.
for form, name in FORMS.items():
    case(
        f"ovs07-override-{form}", "OVS-07", [dec(), idp(), override(name)], "conformant"
    )
case(
    "ovs07-bareid-override-before-decision",
    "OVS-07",
    [override("d1"), idp(), dec()],
    "conformant",
)
case("ovs07-override-unheld", "OVS-07", [dec(), idp(), override("nope")], NC, ["v1"])
# Over-match guard: a name for a held event that is no Decision (here a ToolCall) makes no Override effective.
for form, name in (("bareid", "t9"), ("iri", ref("t9"))):
    case(
        f"ovs07-override-names-held-non-decision-{form}",
        "OVS-07",
        [dec(), ev("t9", "ToolCall", tool={"name": "x"}), idp(), override(name)],
        NC,
        ["v1"],
    )

# TRN-01 / TRN-03: the decision is notifiedBy the Notice that names it.
for form in ("iri", "bareid", "padded"):
    case(f"trn01-notice-{form}", "TRN-01", [dec(), notice(FORMS[form])], "conformant")
case(
    "trn01-bareid-notice-before-decision", "TRN-01", [notice("d1"), dec()], "conformant"
)
case("trn01-notice-unheld", "TRN-01", [dec(), notice("nope")], NC, ["d1"])
for form in ("iri", "bareid"):
    case(f"trn03-notice-{form}", "TRN-03", [dec(), notice(FORMS[form])], "conformant")

# INC-01: an adverse Outcome is linked to the consequential decision it resulted from.
for form in ("iri", "bareid", "padded"):
    case(
        f"inc01-adverse-outcome-{form}",
        "INC-01",
        [dec(), adverse(FORMS[form])],
        "conformant",
    )
case("inc01-adverse-outcome-unheld", "INC-01", [dec(), adverse("nope")], NC, ["o1"])


# INC-03: an Incident naming an Outcome triggers the Outcome's decision only; d1 is reviewed, d2 is not.
def inc03(name):
    return [
        dec(),
        dec("d2"),
        idp(),
        appr("approve"),
        ev("o1", "Outcome", refs={"decision": name}),
        ev(
            "i1",
            "Incident",
            incident_id="i1",
            incident_class="fundamental_rights",
            related_refs=[ref("o1")],
            detected_at="2026-01-10T00:00:00Z",
        ),
    ]


for form in ("iri", "bareid"):
    case(
        f"inc03-incident-via-outcome-{form}", "INC-03", inc03(FORMS[form]), "conformant"
    )
case("inc03-incident-via-outcome-unheld", "INC-03", inc03("nope"), NC, ["d2"])

write()
