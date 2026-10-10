"""Write cases.json for VG-APPROVAL-OUTCOME (18.136). Run from this folder: python make_cases.py.

An approval gates a decision only when its outcome is approve (SPEC §6.2 ApprovalDecided.outcome approve|edit|reject):
CND-02 (gated by a recorded approval) and OVS-08 (a recorded approval decision, two under dual control) count only
approve; OVS-01, INC-03 and RSK-02 ask for a review, which any outcome is. Every approver here meets the SPEC §10.4
human-actor rule unless the case says otherwise. Each case names one control, the profile it runs under (eu: eu-ai-act
and Conduct; nist: nist-ai-rmf), its events and the expected outcome, failing events, quarantined record count and
assess exit code."""

import json
from pathlib import Path

SUBJECT = "spiffe://corp/agents/approval-outcome"
CTX = "https://agent-conformance.org/contexts/evidence/v1"
CLASS = {"Decision": "self_report", "ToolCall": "enforcement_point"}
CHAIN = ["urn:example:service:orchestrator", "urn:example:person:owner-1"]
IDP = "urn:example:service:identity-provider"
NC = "non-conformant"
MISSING = object()


def ev(i, t, **data):
    cls = data.pop("cls", CLASS.get(t, "independent_system"))
    return {
        "specversion": "1.0",
        "id": i,
        "source": f"urn:src:{cls}",
        "subject": SUBJECT,
        "type": f"org.agent-conformance.evidence.{t}.v1",
        "datacontenttype": "application/ld+json",
        "agentcesourceclass": cls,
        "time": "2026-01-10T00:00:00Z",
        "data": {"@context": CTX, "@type": t, "agent": {"id": SUBJECT}, **data},
    }


def ref(i):
    return f"agentce:event/{i}"


def dec(i="d1", **k):
    return ev(i, "Decision", decision_type="dom:CreditDecision", acted_for=CHAIN, **k)


def dual(i="d1"):
    return dec(i, oversight_modality="dual_control")


def ran(d="d1", i="t0"):
    """The enforcement point executed the decision's action."""
    return ev(i, "ToolCall", tool={"name": "x"}, refs={"decision": ref(d)})


def idp(i="s1", who="u2"):
    """The identity-provider login of human `who` (SPEC §10.4)."""
    e = ev(
        i,
        "SessionStart",
        environment="idp-login",
        principal={"id": who, "kind": "human"},
    )
    e["data"]["agent"] = {"id": IDP}
    return e


def appr(outcome, i="a1", d="d1", who="u2", s="s1", kind="human"):
    """An ApprovalDecided on decision d by `who` through login s; outcome=MISSING leaves the outcome out."""
    data = {
        "refs": {"decision": ref(d)},
        "actor": {"id": who, "kind": kind, "authority_ref": "ref:authority/reviewers"},
        "session_ref": ref(s),
    }
    if outcome is not MISSING:
        data["outcome"] = outcome
    return ev(i, "ApprovalDecided", **data)


def incident():
    return ev(
        "i1",
        "Incident",
        incident_id="i1",
        incident_class="fundamental_rights",
        related_refs=[ref("d1")],
        detected_at="2026-01-10T00:00:00Z",
    )


C = {}


def case(
    name, control, events, outcome, failing=(), quarantined=0, profile="eu", exit=2
):
    C[name] = {
        "control": control,
        "profile": profile,
        "events": events,
        "expected": {
            "outcome": outcome,
            "failing": list(failing),
            "quarantined": quarantined,
            "exit": exit,
        },
    }


TWO = [idp("s1"), idp("s2", who="u3")]
# CND-02: the action ran; only an approve outcome gated it.
case("cnd02-approve", "CND-02", [dec(), ran(), idp(), appr("approve")], "conformant")
case(
    "cnd02-reject-only-action-ran",
    "CND-02",
    [dec(), ran(), idp(), appr("reject")],
    NC,
    ["d1"],
)
case(
    "cnd02-edit-only-action-ran",
    "CND-02",
    [dec(), ran(), idp(), appr("edit")],
    NC,
    ["d1"],
)
case(
    "cnd02-missing-outcome", "CND-02", [dec(), ran(), idp(), appr(MISSING)], NC, ["d1"]
)
# An outcome outside approve|edit|reject is schema_invalid: the record is quarantined and gates nothing.
case(
    "cnd02-outcome-approved-spelling",
    "CND-02",
    [dec(), ran(), idp(), appr("approved")],
    NC,
    ["d1"],
    quarantined=1,
)
case(
    "cnd02-reject-by-the-agent",
    "CND-02",
    [dec(), ran(), idp(), appr("reject", who=SUBJECT, kind="agent")],
    NC,
    ["d1"],
)
# A reject and an approve by two verified humans, in either arrival order: the approve gates it.
case(
    "cnd02-reject-then-approve",
    "CND-02",
    [
        dec(),
        ran(),
        *TWO,
        appr("reject", "a1", s="s1"),
        appr("approve", "a2", who="u3", s="s2"),
    ],
    "conformant",
)
case(
    "cnd02-approve-then-reject",
    "CND-02",
    [
        dec(),
        ran(),
        *TWO,
        appr("approve", "a1", s="s1"),
        appr("reject", "a2", who="u3", s="s2"),
    ],
    "conformant",
)
# With no enforcement-point call held, CND-02's minimum evidence is missing, whatever the approval says.
case(
    "cnd02-reject-action-not-run",
    "CND-02",
    [dec(), idp(), appr("reject")],
    "insufficient_evidence",
)
# An approve of another decision gates nothing here.
case(
    "cnd02-approve-names-another-decision",
    "CND-02",
    [
        dec("d1"),
        dec("d2"),
        ran("d1"),
        idp(),
        appr("reject", "a1", d="d1"),
        appr("approve", "a2", d="d2"),
    ],
    NC,
    ["d1"],
)

# OVS-08 (eu-ai-act): one approve, or two from distinct humans under dual control.
case("ovs08-approve", "OVS-08", [dec(), idp(), appr("approve")], "conformant")
case(
    "ovs08-reject-distinct-human",
    "OVS-08",
    [dec(), ran(), idp(), appr("reject")],
    NC,
    ["d1"],
)
case("ovs08-edit", "OVS-08", [dec(), idp(), appr("edit")], NC, ["d1"])
case("ovs08-missing-outcome", "OVS-08", [dec(), idp(), appr(MISSING)], NC, ["d1"])
case(
    "ovs08-dual-two-approves",
    "OVS-08",
    [
        dual(),
        *TWO,
        appr("approve", "a1", s="s1"),
        appr("approve", "a2", who="u3", s="s2"),
    ],
    "conformant",
)
case(
    "ovs08-dual-approve-and-reject",
    "OVS-08",
    [
        dual(),
        *TWO,
        appr("approve", "a1", s="s1"),
        appr("reject", "a2", who="u3", s="s2"),
    ],
    NC,
    ["d1"],
)
case(
    "ovs08-dual-reject-first",
    "OVS-08",
    [
        dual(),
        idp("s2", who="u3"),
        appr("reject", "a2", who="u3", s="s2"),
        idp("s1"),
        appr("approve", "a1", s="s1"),
    ],
    NC,
    ["d1"],
)
case(
    "ovs08-dual-two-rejects",
    "OVS-08",
    [
        dual(),
        *TWO,
        appr("reject", "a1", s="s1"),
        appr("reject", "a2", who="u3", s="s2"),
    ],
    NC,
    ["d1"],
)
# OVS-08 (nist-ai-rmf): its shape asks for one recorded approval decision.
# Every other nist-ai-rmf control the fixture reaches is conformant here, so assess exits 0 when OVS-08 holds and 1
# when it fails.
case(
    "ovs08-nist-approve",
    "OVS-08",
    [dec(), idp(), appr("approve")],
    "conformant",
    profile="nist",
    exit=0,
)
case(
    "ovs08-nist-reject-distinct-human",
    "OVS-08",
    [dec(), ran(), idp(), appr("reject")],
    NC,
    ["d1"],
    profile="nist",
    exit=1,
)
case(
    "ovs08-nist-missing-outcome",
    "OVS-08",
    [dec(), idp(), appr(MISSING)],
    NC,
    ["d1"],
    profile="nist",
    exit=1,
)

# A review is a review whatever its outcome: OVS-01, RSK-02 and INC-03 count a reject; an unverified one never counts.
case("ovs01-reject-is-a-review", "OVS-01", [dec(), idp(), appr("reject")], "conformant")
case("rsk02-reject-is-a-review", "RSK-02", [dec(), idp(), appr("reject")], "conformant")
case(
    "inc03-reject-is-a-review",
    "INC-03",
    [dec(), idp(), appr("reject"), incident()],
    "conformant",
)
case(
    "ovs01-reject-by-the-agent",
    "OVS-01",
    [dec(), idp(), appr("reject", who=SUBJECT, kind="agent")],
    NC,
    ["d1"],
)

Path("cases.json").write_text(
    json.dumps({"cases": C}, indent=2) + "\n", encoding="utf-8"
)
print(len(C), "cases")
