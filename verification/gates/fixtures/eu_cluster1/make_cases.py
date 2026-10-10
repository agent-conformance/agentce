"""Write cases.json for VG-EUAIACT-CLUSTER1 (18.37c). Run from this folder: python make_cases.py.

Each case names one control, its events and the expected outcome, failing events and assess exit code."""

import json
from pathlib import Path

SUBJECT = "spiffe://corp/agents/eu-cluster1"
CTX = "https://agent-conformance.org/contexts/evidence/v1"
CLASS = {"Decision": "self_report", "PolicyDecision": "enforcement_point", "Interrupt": "enforcement_point"}


def ev(i, t, **data):
    cls = data.pop("cls", CLASS.get(t, "independent_system"))
    return {"specversion": "1.0", "id": i, "source": f"urn:src:{cls}", "subject": SUBJECT,
            "type": f"org.agent-conformance.evidence.{t}.v1", "datacontenttype": "application/ld+json",
            "agentcesourceclass": cls, "time": "2026-01-10T00:00:00Z",
            "data": {"@context": CTX, "@type": t, "agent": {"id": SUBJECT}, **data}}


def ref(i):
    return f"agentce:event/{i}"


def dec(i, dtype="dom:CreditDecision", **k):
    return ev(i, "Decision", decision_type=dtype, **k)


def human(pid):
    return {"id": pid, "kind": "human"}


def appr(i, d, actor=None):
    return ev(i, "ApprovalDecided", refs={"decision": ref(d)}, outcome="approve", **({"actor": actor} if actor else {}))


def inc(i, related=(), **k):
    return ev(i, "Incident", incident_id=i, incident_class="fundamental_rights", related_refs=list(related), **k)


def override(i, d, actor=human("u1"), **k):
    return ev(i, "Override", refs={"decision": ref(d)}, actor=actor, original="approve", reason_code="harm", **k)


def interrupt(i, mechanism="kill_switch", effect="halted", actor=human("u1")):
    return ev(i, "Interrupt", effect=effect, **({"mechanism": mechanism} if mechanism else {}), **({"actor": actor} if actor else {}))


DET = {"detected_at": "2026-01-10T00:00:00Z"}
C = {}


def case(name, control, events, outcome, failing=()):
    C[name] = {"control": control, "events": events, "expected": {"outcome": outcome, "failing": list(failing), "exit": None}}


# INC-03: incident-triggering decisions carry an oversight review.
case("inc03-direct-reviewed", "INC-03", [dec("d1"), appr("a1", "d1", human("u1")), inc("i1", [ref("d1")], **DET)], "conformant")
case("inc03-direct-unreviewed", "INC-03", [dec("d1"), dec("d2"), appr("a2", "d2", human("u1")), inc("i1", [ref("d1")], **DET)], "non-conformant", ["d1"])
case("inc03-via-outcome", "INC-03", [dec("d1"), dec("d2"), appr("a2", "d2", human("u1")), ev("o1", "Outcome", refs={"decision": ref("d1")}, adverse=True), inc("i1", [ref("o1")], **DET)], "non-conformant", ["d1"])
case("inc03-via-toolcall", "INC-03", [dec("d1"), dec("d2"), appr("a2", "d2", human("u1")), ev("t1", "ToolCall", cls="enforcement_point", refs={"decision": ref("d1")}, tool={"name": "x"}), inc("i1", [ref("t1")], **DET)], "non-conformant", ["d1"])
case("inc03-via-refs-decision", "INC-03", [dec("d1"), dec("d2"), appr("a2", "d2", human("u1")), inc("i1", refs={"decision": ref("d1")}, **DET)], "non-conformant", ["d1"])
# An incident that names no held decision: the records cannot show which decision triggered it, so every decision is held to the rule.
case("inc03-untraced-incident", "INC-03", [dec("d1"), dec("d2"), appr("a1", "d1", human("u1")), inc("i1", **DET)], "non-conformant", ["d2"])
case("inc03-unresolved-ref", "INC-03", [dec("d1"), dec("d2"), appr("a1", "d1", human("u1")), inc("i1", [ref("zz")], **DET)], "non-conformant", ["d2"])
case("inc03-untraced-all-reviewed", "INC-03", [dec("d1"), appr("a1", "d1", human("u1")), inc("i1", **DET)], "conformant")
# Every name must lead to a held decision; one that does not holds every decision to the rule.
case("inc03-mixed-override", "INC-03", [dec("d1"), dec("d2"), appr("a1", "d1", human("u1")), override("v2", "d2", replacement="reject"), inc("i1", [ref("d1"), ref("v2")], **DET)], "non-conformant", ["d2"])
case("inc03-mixed-override-reviewed", "INC-03", [dec("d1"), dec("d2"), appr("a1", "d1", human("u1")), appr("a2", "d2", human("u1")), override("v2", "d2", replacement="reject"), inc("i1", [ref("d1"), ref("v2")], **DET)], "conformant")
case("inc03-mixed-unresolved", "INC-03", [dec("d1"), dec("d2"), appr("a1", "d1", human("u1")), inc("i1", [ref("d1"), ref("zz")], **DET)], "non-conformant", ["d2"])
case("inc03-mixed-unresolved-reviewed", "INC-03", [dec("d1"), dec("d2"), appr("a1", "d1", human("u1")), appr("a2", "d2", human("u1")), inc("i1", [ref("d1"), ref("zz")], **DET)], "conformant")
case("inc03-no-incident", "INC-03", [dec("d1"), appr("a1", "d1", human("u1"))], "not_applicable")
case("inc03-incident-elsewhere", "INC-03", [dec("d1"), dec("d2"), appr("a1", "d1", human("u1")), inc("i1", [ref("d1")], **DET)], "conformant")
# OVS-01: every consequential decision reviewed (unchanged rule; now the only one in the group).
case("ovs01-all-reviewed", "OVS-01", [dec("d1"), appr("a1", "d1")], "conformant")
case("ovs01-one-unreviewed", "OVS-01", [dec("d1"), dec("d2"), appr("a1", "d1")], "non-conformant", ["d2"])
# OVS-07: overrides and interrupts are effective and recorded by a human.
case("ovs07-effective", "OVS-07", [dec("d1"), override("v1", "d1", replacement="reject"), interrupt("x1")], "conformant")
case("ovs07-paused", "OVS-07", [dec("d1"), interrupt("x1", effect="paused")], "conformant")
case("ovs07-no-replacement", "OVS-07", [dec("d1"), override("v1", "d1")], "non-conformant", ["v1"])
case("ovs07-replacement-same", "OVS-07", [dec("d1"), override("v1", "d1", replacement="approve")], "non-conformant", ["v1"])
case("ovs07-decision-not-held", "OVS-07", [dec("d1"), override("v1", "d9", replacement="reject")], "non-conformant", ["v1"])
case("ovs07-service-actor", "OVS-07", [dec("d1"), override("v1", "d1", actor={"id": "svc", "kind": "service"}, replacement="reject")], "non-conformant", ["v1"])
case("ovs07-degraded", "OVS-07", [dec("d1"), interrupt("x1", effect="degraded")], "non-conformant", ["x1"])
case("ovs07-no-mechanism", "OVS-07", [dec("d1"), interrupt("x1", mechanism=None)], "non-conformant", ["x1"])
case("ovs07-no-actor", "OVS-07", [dec("d1"), interrupt("x1", actor=None)], "non-conformant", ["x1"])
case("ovs07-none", "OVS-07", [dec("d1")], "not_applicable")
# OVS-08: enough distinct human reviewers (two under dual_control).
case("ovs08-one-human", "OVS-08", [dec("d1"), appr("a1", "d1", human("u1"))], "conformant")
case("ovs08-no-actor", "OVS-08", [dec("d1"), appr("a1", "d1")], "non-conformant", ["d1"])
case("ovs08-service-reviewer", "OVS-08", [dec("d1"), appr("a1", "d1", {"id": "svc", "kind": "service"})], "non-conformant", ["d1"])
case("ovs08-dual-two-humans", "OVS-08", [dec("d1", oversight_modality="dual_control"), appr("a1", "d1", human("u1")), appr("a2", "d1", human("u2"))], "conformant")
case("ovs08-dual-same-human-twice", "OVS-08", [dec("d1", oversight_modality="dual_control"), appr("a1", "d1", human("u1")), appr("a2", "d1", human("u1"))], "non-conformant", ["d1"])
case("ovs08-declared-dual-one-human", "OVS-08", [dec("d1", "dom:LargeLoanDecision"), appr("a1", "d1", human("u1"))], "non-conformant", ["d1"])
# ROB-07: incidents are detected and responded to.
case("rob07-responded", "ROB-07", [inc("i1", causal_assessment_at="2026-01-11T00:00:00Z", **DET)], "conformant")
case("rob07-reported", "ROB-07", [inc("i1", reported_at="2026-01-11T00:00:00Z", **DET)], "conformant")
case("rob07-detected-only", "ROB-07", [inc("i1", causal_assessment_at="2026-01-11T00:00:00Z", **DET), inc("i2", **DET)], "non-conformant", ["i2"])
case("rob07-no-detection", "ROB-07", [inc("i1", reported_at="2026-01-11T00:00:00Z")], "non-conformant", ["i1"])
case("rob07-no-incident", "ROB-07", [dec("d1")], "not_applicable")
# RSK-02: a policy decision or a review gates each consequential decision.
case("rsk02-authorized", "RSK-02", [ev("p1", "PolicyDecision", decision="allow", policy_id="p"), dec("d1", refs={"authorization": ref("p1")}), dec("d2"), appr("a2", "d2")], "conformant")
case("rsk02-neither", "RSK-02", [dec("d1"), dec("d2"), appr("a2", "d2")], "non-conformant", ["d1"])
case("rsk02-authorization-dangling", "RSK-02", [dec("d1", refs={"authorization": ref("p1")}), dec("d2"), appr("a2", "d2")], "non-conformant", ["d1"])
case("rsk02-policy-not-exported", "RSK-02", [ev("p1", "PolicyDecision", decision="allow", policy_id="p"), dec("d1", refs={"authorization": ref("p9")}), dec("d2"), appr("a2", "d2")], "non-conformant", ["d1"])
case("rsk02-authorization-not-policy", "RSK-02", [dec("d1", refs={"authorization": ref("a2")}), dec("d2"), appr("a2", "d2")], "non-conformant", ["d1"])
# DAT-03 and RSK-03 are rung 3: not assessed.
case("dat03-rung3", "DAT-03", [dec("d1"), appr("a1", "d1")], "not_assessed")
case("rsk03-rung3", "RSK-03", [dec("d1"), appr("a1", "d1")], "not_assessed")

old = json.loads(Path("cases.json").read_text()) if Path("cases.json").exists() else {"cases": {}}
for name, c in C.items():
    c["expected"]["exit"] = old["cases"].get(name, {}).get("expected", {}).get("exit")
Path("cases.json").write_text(json.dumps({"cases": C}, indent=2) + "\n", encoding="utf-8")
print(len(C), "cases")
