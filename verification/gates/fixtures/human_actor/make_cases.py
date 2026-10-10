"""Write cases.json for VG-HUMAN-ACTOR (18.126). Run from this folder: python make_cases.py.

SPEC §10.4 human-actor rule: an ApprovalDecided, Override or Interrupt counts only when its actor is a human principal
whose session_ref names an identity-provider record the bundle holds from an independent_system or enforcement_point
stream, and whose id appears nowhere in the delegation chain of the activity. Each case names one control, its events and
the expected outcome, failing events, quarantined record count and assess exit code."""

import json
from pathlib import Path

SUBJECT = "spiffe://corp/agents/human-actor"
CTX = "https://agent-conformance.org/contexts/evidence/v1"
CLASS = {"Decision": "self_report", "DelegationIssued": "enforcement_point", "ToolCall": "enforcement_point"}
OWNER = "urn:example:person:owner-1"  # the human the agent acts for: never an independent reviewer
SVC = "urn:example:service:orchestrator"
CHAIN = [SVC, OWNER]


def ev(i, t, **data):
    cls = data.pop("cls", CLASS.get(t, "independent_system"))
    source = data.pop("source", f"urn:src:{cls}")
    return {"specversion": "1.0", "id": i, "source": source, "subject": SUBJECT,
            "type": f"org.agent-conformance.evidence.{t}.v1", "datacontenttype": "application/ld+json",
            "agentcesourceclass": cls, "time": "2026-01-10T00:00:00Z",
            "data": {"@context": CTX, "@type": t, "agent": {"id": SUBJECT}, **data}}


def ref(i):
    return f"agentce:event/{i}"


def dec(i="d1", dtype="dom:CreditDecision", **k):
    return ev(i, "Decision", decision_type=dtype, acted_for=CHAIN, **k)


IDP = "urn:example:service:identity-provider"


def idp(i="s1", cls="independent_system", agent=IDP, **k):
    """The identity-provider login record a session_ref names: a SessionStart of the identity provider, never of the
    assessed agent (the agent's own run session is no human's login)."""
    e = ev(i, "SessionStart", cls=cls, environment="idp-login", **k)
    e["data"]["agent"] = {"id": agent}
    return e


def human(pid):
    return {"id": pid, "kind": "human", "authority_ref": "ref:authority/reviewers"}


def session(s):
    """session_ref: None leaves it out, "bare" writes a bare event id, anything else names that event."""
    if s is None:
        return {}
    return {"session_ref": "s1" if s == "bare" else ref(s)}


def appr(i="a1", d="d1", actor=human("u2"), s="s1", **k):
    return ev(i, "ApprovalDecided", refs={"decision": ref(d)}, outcome="approve",
              **({"actor": actor} if actor else {}), **session(s), **k)


def override(i="v1", d="d1", actor=human("u2"), s="s1", **k):
    return ev(i, "Override", refs={"decision": ref(d)}, actor=actor, original="approve", replacement="reject",
              reason_code="harm", **session(s), **k)


def interrupt(i="x1", actor=human("u2"), s="s1", **k):
    return ev(i, "Interrupt", mechanism="kill_switch", effect="halted", actor=actor, **session(s), **k)


def incident(related):
    return ev("i1", "Incident", incident_id="i1", incident_class="fundamental_rights", related_refs=related,
              detected_at="2026-01-10T00:00:00Z")


C = {}


def case(name, control, events, outcome, failing=(), quarantined=0):
    C[name] = {"control": control, "events": events,
               "expected": {"outcome": outcome, "failing": list(failing), "quarantined": quarantined, "exit": 2}}


NC = "non-conformant"
# OVS-07, Override: the full rule on the actor of an effective override.
case("override-human-verified-session", "OVS-07", [dec(), idp(), override()], "conformant")
case("override-human-verified-enforcement-point-session", "OVS-07", [dec(), idp(cls="enforcement_point"), override()], "conformant")
case("override-session-record-after", "OVS-07", [dec(), override(), idp()], "conformant")
case("override-human-no-session-ref", "OVS-07", [dec(), idp(), override(s=None)], NC, ["v1"])
case("override-session-not-held", "OVS-07", [dec(), idp(), override(s="s9")], NC, ["v1"])
case("override-session-bare-id", "OVS-07", [dec(), idp(), override(s="bare")], NC, ["v1"])
case("override-session-self-report", "OVS-07", [dec(), idp(cls="self_report"), override()], NC, ["v1"])
case("override-session-is-itself", "OVS-07", [dec(), override(s="v1")], NC, ["v1"])
case("override-session-is-a-toolcall", "OVS-07", [dec(), ev("t1", "ToolCall", tool={"name": "x"}), override(s="t1")], NC, ["v1"])
# A record of the identity provider itself that is not a SessionStart: only the type check stops it.
IDP_CALL = idp("t9")
IDP_CALL["type"] = "org.agent-conformance.evidence.ToolCall.v1"
IDP_CALL["data"].update({"@type": "ToolCall", "tool": {"name": "lookup"}})
del IDP_CALL["data"]["environment"]
case("override-session-is-an-idp-toolcall", "OVS-07", [dec(), IDP_CALL, override(s="t9")], NC, ["v1"])
case("override-session-is-the-agents-own-session", "OVS-07", [dec(), idp(cls="enforcement_point", agent=SUBJECT), override()], NC, ["v1"])
case("override-session-is-an-approval", "OVS-07", [dec(), appr(s=None), override(s="a1")], NC, ["v1"])
case("override-session-class-mismatch", "OVS-07", [dec(), idp(source="urn:src:self_report"), override()], NC, ["v1"], 1)
case("override-human-in-delegation-chain", "OVS-07", [dec(), idp(), override(actor=human(OWNER))], NC, ["v1"])
case("override-actor-in-agent-chain-elsewhere", "OVS-07",
     [dec(), idp(), ev("t1", "ToolCall", tool={"name": "x"}, acted_for=[SVC, "u2"]), override()], NC, ["v1"])
case("override-actor-in-delegation-issued-chain", "OVS-07",
     [dec(), idp(), ev("g1", "DelegationIssued", chain=[{"id": SVC, "kind": "service"}, human("u2")]), override()],
     NC, ["v1"])
case("override-actor-in-own-acted-for", "OVS-07", [dec(), idp(), override(acted_for=["u2"])], NC, ["v1"])
case("override-service-actor-with-session", "OVS-07", [dec(), idp(), override(actor={"id": "svc", "kind": "service"})], NC, ["v1"])
case("override-human-blank-id", "OVS-07", [dec(), idp(), override(actor={"id": "", "kind": "human"})], NC, ["v1"])
# OVS-07, Interrupt: the halted agent's own chain.
case("interrupt-human-verified-session", "OVS-07", [dec(), idp(), interrupt()], "conformant")
case("interrupt-human-no-session-ref", "OVS-07", [dec(), idp(), interrupt(s=None)], NC, ["x1"])
case("interrupt-human-in-agent-chain", "OVS-07", [dec(), idp(), interrupt(actor=human(OWNER))], NC, ["x1"])
case("interrupt-session-not-held", "OVS-07", [dec(), interrupt(s="s9")], NC, ["x1"])
case("interrupt-session-self-report", "OVS-07", [dec(), idp(cls="self_report"), interrupt()], NC, ["x1"])


def no_agent(e):
    """The event without its optional agent: the CloudEvents subject still names the assessed agent."""
    del e["data"]["agent"]
    return e


# An oversight record (and its decision) that leaves out the optional agent still concerns the subject.
OWN = dict(cls="enforcement_point", agent=SUBJECT)
case("interrupt-no-agent-verified-session", "OVS-07", [dec(), idp(), no_agent(interrupt())], "conformant")
case("interrupt-no-agent-agents-own-session", "OVS-07", [dec(), idp(**OWN), no_agent(interrupt())], NC, ["x1"])
case("interrupt-no-agent-by-owner", "OVS-07", [dec(), idp(), no_agent(interrupt(actor=human(OWNER)))], NC, ["x1"])
case("interrupt-other-agent-agents-own-session", "OVS-07",
     [dec(), idp(**OWN), interrupt(agent={"id": "spiffe://corp/agents/other"})], NC, ["x1"])
case("override-no-agent-agents-own-session", "OVS-07", [no_agent(dec()), idp(**OWN), no_agent(override())], NC, ["v1"])
case("approval-no-agent-agents-own-session", "OVS-01", [no_agent(dec()), idp(**OWN), no_agent(appr())], NC, ["d1"])
# OVS-01: an approval is a review only under the rule.
case("approval-human-verified-session", "OVS-01", [dec(), idp(), appr()], "conformant")
case("approval-before-decision", "OVS-01", [idp(), appr(), dec()], "conformant")
case("approval-human-no-session-ref", "OVS-01", [dec(), idp(), appr(s=None)], NC, ["d1"])
case("approval-session-not-held", "OVS-01", [dec(), appr(s="s9")], NC, ["d1"])
case("approval-session-self-report", "OVS-01", [dec(), idp(cls="self_report"), appr()], NC, ["d1"])
case("approval-session-is-the-outcome", "OVS-01", [dec(), ev("o1", "Outcome", refs={"decision": ref("d1")}, outcome_type="approved"), appr(s="o1")], NC, ["d1"])
case("approval-session-is-a-policy-decision", "OVS-01", [dec(), ev("p1", "PolicyDecision", cls="enforcement_point", decision="allow", policy_id="p"), appr(s="p1")], NC, ["d1"])
case("approval-session-is-an-idp-toolcall", "OVS-01", [dec(), IDP_CALL, appr(s="t9")], NC, ["d1"])
case("approval-session-is-the-agents-own-session", "OVS-01", [dec(), idp(cls="enforcement_point", agent=SUBJECT), appr()], NC, ["d1"])
case("approval-human-in-delegation-chain", "OVS-01", [dec(), idp(), appr(actor=human(OWNER))], NC, ["d1"])
case("approval-no-actor-with-session", "OVS-01", [dec(), idp(), appr(actor=None)], NC, ["d1"])
# OVS-08: distinct verified humans; two approvals that resolve to one identity-provider record are one human.
case("ovs08-verified-human", "OVS-08", [dec(), idp(), appr()], "conformant")
case("dual-control-two-verified-humans", "OVS-08",
     [dec(oversight_modality="dual_control"), idp("s1"), idp("s2"), appr("a1", actor=human("u2"), s="s1"),
      appr("a2", actor=human("u3"), s="s2")], "conformant")
case("dual-control-one-human-two-ids", "OVS-08",
     [dec(oversight_modality="dual_control"), idp("s1"), appr("a1", actor=human("u2"), s="s1"),
      appr("a2", actor=human("u2 "), s="s1")], NC, ["d1"])
case("dual-control-same-id-two-sessions", "OVS-08",
     [dec(oversight_modality="dual_control"), idp("s1"), idp("s2"), appr("a1", actor=human("u2"), s="s1"),
      appr("a2", actor=human("u2"), s="s2")], NC, ["d1"])
case("dual-control-one-verified-one-not", "OVS-08",
     [dec(oversight_modality="dual_control"), idp("s1"), appr("a1", actor=human("u2"), s="s1"),
      appr("a2", actor=human("u3"), s=None)], NC, ["d1"])
case("dual-control-one-in-chain", "OVS-08",
     [dec(oversight_modality="dual_control"), idp("s1"), idp("s2"), appr("a1", actor=human("u2"), s="s1"),
      appr("a2", actor=human(OWNER), s="s2")], NC, ["d1"])
GW = ev("t0", "ToolCall", tool={"name": "x"}, refs={"decision": ref("d1")})  # CND-02 needs an enforcement-point call
# The other controls that count a review: CND-02 (Conduct), RSK-02 and INC-03.
case("cnd02-approval-verified", "CND-02", [dec(), GW, idp(), appr()], "conformant")
case("cnd02-approval-no-session-ref", "CND-02", [dec(), GW, idp(), appr(s=None)], NC, ["d1"])
case("rsk02-review-verified", "RSK-02", [dec(), idp(), appr()], "conformant")
case("rsk02-review-in-delegation-chain", "RSK-02", [dec(), idp(), appr(actor=human(OWNER))], NC, ["d1"])
case("inc03-review-verified", "INC-03", [dec(), idp(), appr(), incident([ref("d1")])], "conformant")
case("inc03-review-no-session-ref", "INC-03", [dec(), idp(), appr(s=None), incident([ref("d1")])], NC, ["d1"])

Path("cases.json").write_text(json.dumps({"cases": C}, indent=2) + "\n", encoding="utf-8")
print(len(C), "cases")
