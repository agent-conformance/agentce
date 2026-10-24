"""Write cases.json for VG-EUAIACT-CLUSTER3 (18.37f). Run from this folder: python make_cases.py.

Each case names one control, its events, the profile it runs under, how many records are quarantined, and the
expected outcome, failing events and assess exit code. The exit codes are kept from the cases.json already written
(captured from the real CLIs)."""

import json
from pathlib import Path

SUBJECT = "spiffe://corp/agents/eu-cluster3"
CTX = "https://agent-conformance.org/contexts/evidence/v1"
T0 = "2026-01-10T00:00:00Z"
EARLIER = "2026-01-09T00:00:00Z"
LATER = "2026-01-11T00:00:00Z"
CLASS = {"Decision": "self_report", "Notice": "independent_system", "Disclosure": "independent_system",
         "PolicyDecision": "enforcement_point", "SessionStart": "independent_system",
         "ApprovalDecided": "independent_system"}
DIGEST = "sha256:" + "3" * 64


def ev(i, t, at=T0, **data):
    cls = data.pop("cls", CLASS.get(t, "enforcement_point"))
    agent = data.pop("agent", {"id": SUBJECT})
    return {"specversion": "1.0", "id": i, "source": f"urn:src:{cls}", "subject": SUBJECT,
            "type": f"org.agent-conformance.evidence.{t}.v1", "datacontenttype": "application/ld+json",
            "agentcesourceclass": cls, "time": at,
            "data": {"@context": CTX, "@type": t, "agent": agent, **data}}


def ref(i):
    return f"agentce:event/{i}"


def dec(i, dtype="dom:CreditDecision", **k):
    return ev(i, "Decision", decision_type=dtype, **k)


CHOICE = {"options": [{"id": "approve", "label": "Approve"}, {"id": "decline", "label": "Decline"}], "chosen": "approve"}


def documented(i="d1", **k):
    return dec(i, **CHOICE, **k)


def policy(i, request=None, **k):
    data = {"engine": "opa", "policy_id": "credit", "policy_version": "1", "policy_digest": DIGEST,
            "decision": "allow", "reasons": ["r1"], "obligations": [], "principal": {"id": SUBJECT, "kind": "agent"}}
    data.update(k)
    if request:
        data["refs"] = {"request": ref(request)}
    return ev(i, "PolicyDecision", **data)


def authorized(i="d1", pd="p1", **k):
    return dec(i, refs={"authorization": ref(pd)}, **k)


def notice(i, d="d1", notice_type="subject_of_ai_decision", **k):
    data = {"person_ref": "agentce:principal/applicant-fictional-0001", "notice_type": notice_type,
            "delivered_at": T0, "channel": "email", "content_ref": DIGEST, "refs": {"decision": d and ref(d)}}
    data.update(k)
    return ev(i, "Notice", **data)


def disclosure(i, session="s-1", disclosure_type="ai_interaction", **k):
    data = {"disclosure_type": disclosure_type, "delivered_at": T0, "mechanism": "banner", "session_id": session}
    data.update(k)
    return ev(i, "Disclosure", **data)


def person_dec(i="d1", **k):
    return dec(i, **{"affects_natural_person": True, "session_id": "s-1", **k})


def approval(i, d="d1", at=T0):
    """A human approval that meets the SPEC §10.4 human-actor rule: its session_ref names a held login of the same
    human, from an independent system whose agent is not the subject's."""
    return [ev("login", "SessionStart", at=EARLIER, agent={"id": "spiffe://corp/idp"},
               principal={"id": "user:bob", "kind": "human"}),
            ev(i, "ApprovalDecided", at=at, actor={"id": "user:bob", "kind": "human", "authority_ref": "role:officer"},
               session_ref=ref("login"), outcome="approve", latency_ms=10, refs={"decision": ref(d)})]


C = {}


def case(name, control, events, outcome, failing=(), quarantined=0, profile="applicability.yaml"):
    C[name] = {"control": control, "events": events, "profile": profile, "quarantined": quarantined,
               "expected": {"outcome": outcome, "failing": list(failing), "exit": None}}


NC = "non-conformant"
CF = "conformant"
IE = "insufficient_evidence"
NA = "not_applicable"

# REC-02 keeps its rule: a consequential decision carries its time (Art. 12).
case("rec02-timestamped", "REC-02", [dec("d1")], CF)
case("rec02-not-consequential", "REC-02", [dec("d1", "dom:Lookup")], NA)

# DOC-02: the decision documents what it chose, with its time of record (Art. 13).
case("doc02-chosen-in-options", "DOC-02", [documented()], CF)
case("doc02-chosen-no-options", "DOC-02", [dec("d1", chosen="approve")], CF)
case("doc02-no-chosen", "DOC-02", [dec("d1", options=[{"id": "approve"}])], NC, ["d1"])
case("doc02-empty-chosen", "DOC-02", [dec("d1", chosen="")], NC, ["d1"])
case("doc02-chosen-not-in-options", "DOC-02", [dec("d1", options=[{"id": "approve"}, {"id": "decline"}],
     chosen="refer")], NC, ["d1"])
# The schema refuses an options entry that is not an object, so the decision is quarantined and nothing is judged.
case("doc02-options-not-objects", "DOC-02", [dec("d1", options=["approve"], chosen="approve")], IE, quarantined=1)
case("doc02-one-of-two", "DOC-02", [documented("d1"), dec("d2")], NC, ["d2"])
case("doc02-not-consequential", "DOC-02", [dec("d1", "dom:Lookup")], NA)

# TRN-04: an explanation Notice names the decision and was delivered at or after its time.
case("trn04-explanation-after", "TRN-04", [dec("d1"), notice("n1", notice_type="explanation", delivered_at=LATER)], CF)
case("trn04-explanation-same-time", "TRN-04", [dec("d1"), notice("n1", notice_type="explanation")], CF)
case("trn04-explanation-before", "TRN-04", [dec("d1"), notice("n1", notice_type="explanation", delivered_at=EARLIER)],
     NC, ["d1"])
case("trn04-other-type", "TRN-04", [dec("d1"), notice("n1", delivered_at=LATER)], NC, ["d1"])
case("trn04-no-delivered-at", "TRN-04", [dec("d1"), notice("n1", notice_type="explanation", delivered_at=None)], NC,
     ["d1"])
case("trn04-delivered-unparseable", "TRN-04", [dec("d1"), notice("n1", notice_type="explanation",
     delivered_at="next tuesday")], NC, ["d1"])
case("trn04-other-decision", "TRN-04", [dec("d1"), dec("d2", "dom:Lookup"),
     notice("n1", d="d2", notice_type="explanation")], NC, ["d1"])
case("trn04-bare-id", "TRN-04", [dec("d1"), notice("n1", notice_type="explanation", refs={"decision": "d1"})], CF)
case("trn04-no-notice", "TRN-04", [dec("d1")], IE)
case("trn04-one-of-two", "TRN-04", [dec("d1"), dec("d2"), notice("n1", notice_type="explanation")], NC, ["d2"])
case("trn04-order-reversed", "TRN-04", [notice("n1", notice_type="explanation"), dec("d1")], CF)
case("trn04-notice-quarantined", "TRN-04", [dec("d1"), notice("n1", notice_type="explanation", person_ref=7)], IE,
     quarantined=1)
case("trn04-not-consequential", "TRN-04", [dec("d1", "dom:Lookup"), notice("n1", notice_type="explanation")], NA)

# OVS-05, RSK-01, REC-05 share the held-PolicyDecision base (Q1 B, Q12 B); each then tests its own clause.
for c in ("OVS-05", "RSK-01", "REC-05"):
    k = c.lower().replace("-", "")
    case(f"{k}-held", c, [policy("p1", request="d1"), authorized()], CF)
    case(f"{k}-request-only", c, [policy("p1", request="d1"), dec("d1")], CF)
    case(f"{k}-authorization-only", c, [policy("p1"), authorized()], CF)
    case(f"{k}-sr-pd", c, [policy("p1", cls="self_report"), authorized()], CF)
    case(f"{k}-none", c, [policy("p9", request="x9"), dec("d1")], NC, ["d1"])
    case(f"{k}-no-pd", c, [dec("d1")], IE)
    case(f"{k}-deny", c, [policy("p1", decision="deny"), authorized()], NC, ["d1"])
    case(f"{k}-unheld", c, [policy("p9"), authorized(pd="zz")], NC, ["d1"])
    case(f"{k}-non-pd", c, [policy("p9"), dec("d2", "dom:Lookup"), authorized(pd="d2")], NC, ["d1"])
    case(f"{k}-bare", c, [policy("p1", request="d1"), dec("d1", refs={"authorization": "p1"})], NC, ["d1"])
    case(f"{k}-deny-via-request", c, [policy("p1"), policy("p2", request="d1", decision="deny"), authorized()], NC,
         ["d1"])
    case(f"{k}-order-reversed", c, [authorized(), policy("p1")], CF)
    # A gate the engine quarantined is not held, so the decision names no held gate; quarantine never reads better.
    case(f"{k}-pd-quarantined", c, [policy("p1", decision=7), policy("p9"), authorized()], NC, ["d1"], quarantined=1)
    # The schema quarantines a non-string reference, so the decision is not held and nothing is judged.
    case(f"{k}-authorization-not-string", c, [policy("p1", request="d1"), dec("d1", refs={"authorization": 7})], IE,
         quarantined=1)
    case(f"{k}-not-consequential", c, [policy("p1"), authorized(dtype="dom:Lookup")], NA)

# OVS-05: any held, non-deny gate counts.
case("ovs05-transform", "OVS-05", [policy("p1", decision="transform"), authorized()], CF)
case("ovs05-require-approval", "OVS-05", [policy("p1", decision="require_approval"), authorized()], CF)
# RSK-01: the gate was cleared: allow, or require_approval released by a human approval.
# transform clears nothing: SPEC OVS-03 E1 reads "decision=allow or require_approval satisfied".
case("rsk01-transform", "RSK-01", [policy("p1", decision="transform"), authorized()], NC, ["d1"])
case("rsk01-require-approval-unapproved", "RSK-01", [policy("p1", decision="require_approval"), authorized()], NC,
     ["d1"])
case("rsk01-require-approval-approved", "RSK-01",
     [policy("p1", decision="require_approval"), authorized()] + approval("a1"), CF)
case("rsk01-approval-after-action", "RSK-01",
     [policy("p1", decision="require_approval"), authorized(),
      ev("t1", "ToolCall", tool={"name": "x"}, refs={"decision": ref("d1")})] + approval("a1", at=LATER), NC, ["d1"])
case("rsk01-missing-decision-value", "RSK-01", [policy("p1", decision=None), authorized()], NC, ["d1"])
case("rsk01-one-unapproved", "RSK-01", [policy("p1"), policy("p2", request="d1", decision="require_approval"),
     authorized()], NC, ["d1"])
# REC-05: the gate names the policy it applied: id, version and a SPEC Digest.
case("rec05-no-digest", "REC-05", [policy("p1", policy_digest=None), authorized()], NC, ["d1"])
case("rec05-digest-malformed", "REC-05", [policy("p1", policy_digest="sha256:XYZ"), authorized()], NC, ["d1"])
case("rec05-empty-version", "REC-05", [policy("p1", policy_version=""), authorized()], NC, ["d1"])
case("rec05-no-policy-id", "REC-05", [policy("p1", policy_id=None), authorized()], NC, ["d1"])
case("rec05-one-incomplete", "REC-05", [policy("p1"), policy("p2", request="d1", policy_digest=None), authorized()],
     NC, ["d1"])

# TRN-01: the affected person was notified: a Notice naming the decision, with a person and a delivery time.
case("trn01-notified", "TRN-01", [dec("d1", person_ref="agentce:principal/applicant-fictional-0001"), notice("n1")],
     CF)
case("trn01-decision-no-person", "TRN-01", [dec("d1"), notice("n1")], CF)
case("trn01-no-person-ref", "TRN-01", [dec("d1"), notice("n1", person_ref=None)], NC, ["d1"])
case("trn01-no-delivered-at", "TRN-01", [dec("d1"), notice("n1", delivered_at=None)], NC, ["d1"])
case("trn01-person-mismatch", "TRN-01", [dec("d1", person_ref="agentce:principal/someone-else"), notice("n1")], NC,
     ["d1"])
case("trn01-other-decision", "TRN-01", [dec("d1"), dec("d2", "dom:Lookup"), notice("n1", d="d2")], NC, ["d1"])
case("trn01-no-notice", "TRN-01", [dec("d1")], IE)
case("trn01-one-of-two", "TRN-01", [dec("d1"), dec("d2"), notice("n1")], NC, ["d2"])
case("trn01-order-reversed", "TRN-01", [notice("n1"), dec("d1")], CF)
case("trn01-not-consequential", "TRN-01", [dec("d1", "dom:Lookup"), notice("n1")], NA)

# TRN-05: where a decision affects a natural person, an AI-interaction Disclosure in its session came first (Art. 50).
case("trn05-disclosed", "TRN-05", [disclosure("x1", delivered_at=EARLIER), person_dec()], CF)
case("trn05-same-time", "TRN-05", [disclosure("x1"), person_dec()], CF)
case("trn05-after", "TRN-05", [disclosure("x1", delivered_at=LATER), person_dec()], NC, ["d1"])
case("trn05-other-session", "TRN-05", [disclosure("x1", session="s-2"), person_dec()], NC, ["d1"])
case("trn05-decision-no-session", "TRN-05", [disclosure("x1"), person_dec(session_id=None)], NC, ["d1"])
case("trn05-other-type", "TRN-05", [disclosure("x1", disclosure_type="deepfake_label"), person_dec()], NC, ["d1"])
case("trn05-no-disclosure", "TRN-05", [person_dec()], IE)
case("trn05-one-of-two", "TRN-05", [disclosure("x1"), person_dec("d1"), person_dec("d2", session_id="s-2")], NC,
     ["d2"])
case("trn05-order-reversed", "TRN-05", [person_dec(), disclosure("x1")], CF)
case("trn05-disclosure-quarantined", "TRN-05", [disclosure("x1", mechanism=7), person_dec()], IE, quarantined=1)
# Only an explicit false takes a decision out of scope; a missing member fails closed (approvals ruling Q7).
case("trn05-no-person", "TRN-05", [disclosure("x1"), dec("d1", session_id="s-1")], CF)
case("trn05-no-person-after", "TRN-05", [disclosure("x1", delivered_at=LATER), dec("d1", session_id="s-1")], NC, ["d1"])
case("trn05-no-person-no-disclosure", "TRN-05", [dec("d1", session_id="s-1")], IE)
case("trn05-person-not-bool", "TRN-05", [disclosure("x1", delivered_at=LATER), person_dec(affects_natural_person="yes")], IE,
     quarantined=1)
case("trn05-person-false", "TRN-05", [disclosure("x1"), person_dec(affects_natural_person=False)], NA)
case("trn05-not-consequential", "TRN-05", [disclosure("x1"), person_dec(dtype="dom:Lookup")], NA)

# A None member is left out of the record (the helpers pass None to drop a member).
for c in C.values():
    for e in c["events"]:
        e["data"] = {k: v for k, v in e["data"].items() if v is not None}

old = json.loads(Path("cases.json").read_text()) if Path("cases.json").exists() else {"cases": {}}
for name, c in C.items():
    c["expected"]["exit"] = old["cases"].get(name, {}).get("expected", {}).get("exit")
Path("cases.json").write_text(json.dumps({"cases": C}, indent=2) + "\n", encoding="utf-8")
print(len(C), "cases")
