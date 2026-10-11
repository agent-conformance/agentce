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


def tool(name, i="t0", **k):
    """The enforcement point ran a tool for the decision, naming it as `name`."""
    return ev(i, "ToolCall", tool={"name": "x"}, refs={"decision": name, **k})


def override(name, i="v1", who="u2", s="s1"):
    """A human's Override of the decision, naming it as `name`, with a replacement that differs (OVS-07)."""
    actor = {"id": who, "kind": "human", "authority_ref": "ref:authority/reviewers"}
    return ev(
        i,
        "Override",
        refs={"decision": name},
        actor=actor,
        session_ref=ref(s),
        original="approve",
        replacement="reject",
        reason_code="harm",
    )


def notice(name, i="n1"):
    """The affected person's Notice of the decision, naming it as `name` (TRN-01, TRN-03)."""
    return ev(i, "Notice", refs={"decision": name}, notice_type="adverse_action")


def adverse(name, i="o1"):
    """An adverse Outcome of the decision, naming it as `name` (INC-01)."""
    return ev(i, "Outcome", refs={"decision": name}, adverse=True)


# CND-04 (Conduct): a ToolCall that executes a consequential decision must act on a named instruction. The ToolCall
# names no instruction, so it is in CND-04's scope and fails whenever its decision is resolved; with a ref naming no
# held decision it executes nothing consequential and is out of scope (not_applicable), as at base.
for form, name in FORMS.items():
    case(
        f"cnd04-consequential-toolcall-{form}",
        "CND-04",
        [dec(), tool(name)],
        NC,
        ["t0"],
    )
case("cnd04-bareid-toolcall-before-decision", "CND-04", [tool("d1"), dec()], NC, ["t0"])
case("cnd04-consequential-toolcall-unheld", "CND-04", [dec(), tool("nope")], NA)
# Over-match guard: a bare id naming a held event that is no Decision executes nothing consequential.
case(
    "cnd04-bareid-names-held-non-decision",
    "CND-04",
    [dec(), ev("o1", "Outcome", refs={"decision": IRI}), tool("o1")],
    NA,
)
# Positive twin: the bare-id ToolCall acting on an instruction the bundle holds passes CND-04.
case(
    "cnd04-bareid-toolcall-acts-on-instruction",
    "CND-04",
    [
        dec(),
        ev("r1", "Instruction", instruction_id="r1", source_class="tool_output"),
        tool("d1", instruction=ref("r1")),
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
