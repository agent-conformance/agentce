"""Event builders and the case table shared by the approval gate fixtures (VG-APPROVAL-OUTCOME, VG-APPROVAL-ORDER):
one subject, a high-effect decision, its enforcement-point action, identity-provider logins and the ApprovalDecided
records on it. Every approver meets the SPEC §10.4 human-actor rule unless a case says otherwise; `at` sets an event's
time (all at AT by default)."""

import json
from pathlib import Path

SUBJECT = "spiffe://corp/agents/approval-outcome"
CTX = "https://agent-conformance.org/contexts/evidence/v1"
CLASS = {"Decision": "self_report", "ToolCall": "enforcement_point"}
CHAIN = ["urn:example:service:orchestrator", "urn:example:person:owner-1"]
IDP = "urn:example:service:identity-provider"
NC = "non-conformant"
MISSING = object()
AT = "2026-01-10T00:00:00Z"


def ev(i, t, at=AT, **data):
    cls = data.pop("cls", CLASS.get(t, "independent_system"))
    return {
        "specversion": "1.0",
        "id": i,
        "source": f"urn:src:{cls}",
        "subject": SUBJECT,
        "type": f"org.agent-conformance.evidence.{t}.v1",
        "datacontenttype": "application/ld+json",
        "agentcesourceclass": cls,
        "time": at,
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
    name,
    control,
    events,
    outcome,
    failing=(),
    quarantined=0,
    profile="eu",
    exit=2,
    integrity=None,
    streams=None,
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
    if integrity is not None:
        C[name]["expected"]["integrity"] = integrity
    if streams is not None:
        C[name]["streams"] = streams


TWO = [idp("s1"), idp("s2", who="u3")]


def write():
    Path("cases.json").write_text(
        json.dumps({"cases": C}, indent=2) + "\n", encoding="utf-8"
    )
    print(len(C), "cases")
