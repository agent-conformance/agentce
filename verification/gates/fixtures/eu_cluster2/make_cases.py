"""Write cases.json for VG-EUAIACT-CLUSTER2 (18.37e). Run from this folder: python make_cases.py.

Each case names one control, its events, the profile it runs under (applicability*.yaml), any extra records a
denominator counts beyond the captured ones, how many records are quarantined, and the expected outcome, failing events
and assess exit code. A catalog case instead names a requires_denominator value written into DOC-03 and the message key
assess refuses it with."""

import json
from pathlib import Path

SUBJECT = "spiffe://corp/agents/eu-cluster2"
CTX = "https://agent-conformance.org/contexts/evidence/v1"
CLASS = {"Decision": "self_report", "Incident": "independent_system", "ToolCall": "enforcement_point"}


def ev(i, t, **data):
    cls = data.pop("cls", CLASS.get(t, "enforcement_point"))
    return {"specversion": "1.0", "id": i, "source": f"urn:src:{cls}", "subject": SUBJECT,
            "type": f"org.agent-conformance.evidence.{t}.v1", "datacontenttype": "application/ld+json",
            "agentcesourceclass": cls, "time": "2026-01-10T00:00:00Z",
            "data": {"@context": CTX, "@type": t, "agent": {"id": SUBJECT}, **data}}


def ref(i):
    return f"agentce:event/{i}"


def dec(i, dtype="dom:CreditDecision", **k):
    return ev(i, "Decision", decision_type=dtype, **k)


def call(i, d="d1", cls="enforcement_point", **refs):
    """A ToolCall; with ``d`` it executes that decision (consequential when the decision's type is)."""
    r = {"decision": ref(d)} if d else {}
    r.update(refs)
    return ev(i, "ToolCall", cls=cls, tool={"name": "x"}, **({"refs": r} if r else {}))


def policy(i, **k):
    return ev(i, "PolicyDecision", **{"decision": "allow", "policy_id": "p", **k})


def delegation(i):
    return ev(i, "DelegationIssued", token_ref="jti-1", issuer="https://idp.example",
              chain=[{"id": "user:alice", "kind": "human"}, {"id": SUBJECT, "kind": "agent"}],
              subject_principal="user:alice", actor_principal=SUBJECT, scope_granted=["credit:read"],
              scope_parent=["credit:read"], expires="2026-02-01T00:00:00Z",
              verification={"status": "verified", "method": "rfc8693"})


def instruction(i):
    return ev(i, "Instruction", source_class="user", principal={"id": "user:alice", "kind": "human"})


def inc(i, related=(), **k):
    return ev(i, "Incident", incident_id=i, incident_class="fundamental_rights", detected_at="2026-01-10T00:00:00Z",
              related_refs=list(related), **k)


C = {}


def case(name, control, events, outcome, failing=(), profile="applicability.yaml", extra=None, quarantined=0):
    C[name] = {"control": control, "events": events, "profile": profile, "extra": extra or {},
               "quarantined": quarantined, "expected": {"outcome": outcome, "failing": list(failing), "exit": None}}


def catalog_case(name, value, key="catalog.control.requires_denominator_invalid"):
    C[name] = {"control": "DOC-03", "catalog_value": value, "events": [dec("d1"), call("t1")],
               "profile": "applicability-egress.yaml", "extra": {}, "quarantined": 0,
               "expected": {"key": key, "exit": None}}


EP_CALL = [dec("d1"), call("t1")]
SELF_CALL = [dec("d1"), call("t1", cls="self_report")]

# DOC-03: a consequential call at an enforcement point, and a declared egress-proxy or firewall count that reconciles
# the captured tool calls (ruling 19 B: with no such declaration the comparison cannot be made).
case("doc03-egress-covered", "DOC-03", EP_CALL, "conformant", profile="applicability-egress.yaml")
case("doc03-firewall-covered", "DOC-03", EP_CALL, "conformant", profile="applicability-firewall.yaml")
case("doc03-egress-beside-sor", "DOC-03", EP_CALL, "conformant", profile="applicability-egress-sor.yaml")
case("doc03-sor-only", "DOC-03", EP_CALL, "insufficient_evidence")
case("doc03-no-denominator", "DOC-03", EP_CALL, "insufficient_evidence", profile="applicability-none.yaml")
case("doc03-attestation-only", "DOC-03", EP_CALL, "insufficient_evidence", profile="applicability-attestation.yaml")
case("doc03-egress-above", "DOC-03", EP_CALL, "insufficient_evidence", profile="applicability-egress.yaml",
     extra={"egress_proxy_log": {"ToolCall": 1}})
# A declared egress path that saw fewer calls than the enforcement point recorded does not match it either.
case("doc03-egress-fewer", "DOC-03", EP_CALL + [call("t2")], "insufficient_evidence", profile="applicability-egress.yaml",
     extra={"egress_proxy_log": {"ToolCall": -1}})
case("doc03-egress-zero", "DOC-03", EP_CALL, "insufficient_evidence", profile="applicability-egress.yaml",
     extra={"egress_proxy_log": {"ToolCall": -1}})
case("doc03-sor-holds-count", "DOC-03", EP_CALL, "insufficient_evidence", profile="applicability-egress-sor.yaml",
     extra={"system_of_record_export": {"ToolCall": 1}})
case("doc03-not-ep-egress", "DOC-03", SELF_CALL, "non-conformant", ["t1"], profile="applicability-egress.yaml")
case("doc03-not-ep-no-denominator", "DOC-03", SELF_CALL, "non-conformant", ["t1"], profile="applicability-none.yaml")
case("doc03-no-consequential", "DOC-03", [dec("d1", "dom:Lookup"), call("t1")], "not_applicable",
     profile="applicability-egress.yaml")
case("doc03-no-consequential-sor", "DOC-03", [dec("d1", "dom:Lookup"), call("t1")], "insufficient_evidence")
# OVS-06 keeps the class-only rule.
case("ovs06-ep", "OVS-06", EP_CALL, "conformant")
case("ovs06-not-ep", "OVS-06", SELF_CALL, "non-conformant", ["t1"])
case("ovs06-no-denominator", "OVS-06", EP_CALL, "conformant", profile="applicability-none.yaml")
# ROB-05 and ROB-06 are rung 3: no engine measures probe or budget results.
case("rob05-rung3", "ROB-05", EP_CALL, "not_assessed")
case("rob05-rung3-not-ep", "ROB-05", SELF_CALL, "not_assessed")
case("rob06-rung3", "ROB-06", EP_CALL, "not_assessed")
case("rob06-rung3-not-ep", "ROB-06", SELF_CALL, "not_assessed")
# INT-03: a consequential call's own record carries no dangling reference.
case("int03-clean", "INT-03", [policy("p1"), dec("d1"), call("t1", authorization=ref("p1"))], "conformant")
case("int03-dangling", "INT-03", [dec("d1"), call("t1", authorization=ref("zz"))], "non-conformant", ["t1"])
case("int03-used-dangling", "INT-03", [dec("d1"), dict(call("t1"), data=dict(call("t1")["data"], used=[ref("zz")]))],
     "non-conformant", ["t1"])
case("int03-dangling-not-consequential", "INT-03",
     [dec("d1"), dec("d2", "dom:Lookup"), call("t1"), call("t2", d="d2", authorization=ref("zz"))], "conformant")
case("int03-no-consequential", "INT-03", [dec("d2", "dom:Lookup"), call("t2", d="d2")], "not_applicable")
# ROB-03: refs.authorization, refs.delegation and refs.instruction name held events of their SPEC type.
case("rob03-all-held", "ROB-03", [policy("p1"), delegation("g1"), instruction("n1"), dec("d1"),
     call("t1", authorization=ref("p1"), delegation=ref("g1"), instruction=ref("n1"))], "conformant")
case("rob03-none", "ROB-03", EP_CALL, "conformant")
case("rob03-order-reversed", "ROB-03", [dec("d1"), call("t1", authorization=ref("p1")), policy("p1")], "conformant")
case("rob03-authorization-dangling", "ROB-03", [dec("d1"), call("t1", authorization=ref("zz"))], "non-conformant", ["t1"])
case("rob03-delegation-dangling", "ROB-03", [dec("d1"), call("t1", delegation=ref("zz"))], "non-conformant", ["t1"])
case("rob03-instruction-dangling", "ROB-03", [dec("d1"), call("t1", instruction=ref("zz"))], "non-conformant", ["t1"])
case("rob03-authorization-wrong-type", "ROB-03", [dec("d1"), call("t1", authorization=ref("d1"))], "non-conformant",
     ["t1"])
case("rob03-delegation-wrong-type", "ROB-03", [policy("p1"), dec("d1"), call("t1", delegation=ref("p1"))],
     "non-conformant", ["t1"])
case("rob03-instruction-wrong-type", "ROB-03", [delegation("g1"), dec("d1"), call("t1", instruction=ref("g1"))],
     "non-conformant", ["t1"])
case("rob03-other-ref-dangling", "ROB-03", [dec("d1"), dict(call("t1"), data=dict(call("t1")["data"], used=[ref("zz")]))],
     "conformant")
case("rob03-not-consequential", "ROB-03", [dec("d2", "dom:Lookup"), call("t2", d="d2", authorization=ref("zz"))],
     "not_applicable")
case("rob03-one-of-two", "ROB-03", [policy("p1"), dec("d1"), call("t1", authorization=ref("p1")),
     call("t2", authorization=ref("d1"))], "non-conformant", ["t2"])
# REC-06: no record reachable from a consequential call carries a dangling reference.
case("rec06-clean", "REC-06", [policy("p1"), dec("d1"), call("t1", authorization=ref("p1"))], "conformant")
case("rec06-own-dangling", "REC-06", [dec("d1"), call("t1", authorization=ref("zz"))], "non-conformant", ["t1"])
case("rec06-named-dangling", "REC-06", [policy("p1", refs={"request": ref("zz")}), dec("d1"),
     call("t1", authorization=ref("p1"))], "non-conformant", ["t1"])
case("rec06-decision-dangling", "REC-06", [dec("d1", refs={"authorization": ref("zz")}), call("t1")], "non-conformant",
     ["t1"])
case("rec06-depth-two", "REC-06", [policy("p2", refs={"request": ref("zz")}), policy("p1", refs={"request": ref("p2")}),
     dec("d1"), call("t1", authorization=ref("p1"))], "non-conformant", ["t1"])
case("rec06-depth-two-clean", "REC-06", [policy("p2"), policy("p1", refs={"request": ref("p2")}), dec("d1"),
     call("t1", authorization=ref("p1"))], "conformant")
case("rec06-cycle", "REC-06", [policy("p1", refs={"request": ref("p2")}), policy("p2", refs={"request": ref("p1")}),
     dec("d1"), call("t1", authorization=ref("p1"))], "conformant")
case("rec06-cycle-dangling", "REC-06", [policy("p1", refs={"request": ref("p2")}),
     policy("p2", refs={"request": ref("p1"), "decision": ref("zz")}), dec("d1"), call("t1", authorization=ref("p1"))],
     "non-conformant", ["t1"])
case("rec06-order-reversed", "REC-06", [dec("d1"), call("t1", authorization=ref("p1")),
     policy("p1", refs={"request": ref("zz")})], "non-conformant", ["t1"])
# A named record the engine quarantines (a decision field that is not a string) is not held, so the name dangles.
case("rec06-named-quarantined", "REC-06", [policy("p1", decision=7), dec("d1"), call("t1", authorization=ref("p1"))],
     "non-conformant", ["t1"], quarantined=1)
case("inc04-named-quarantined", "INC-04", [policy("p1", decision=7), inc("i1", [ref("p1")])], "non-conformant", ["i1"],
     quarantined=1)
case("rec06-not-consequential", "REC-06", [dec("d2", "dom:Lookup"), call("t2", d="d2", authorization=ref("zz"))],
     "not_applicable")
# INC-04: no record reachable from an incident carries a dangling reference; an unresolved related_refs entry is one.
case("inc04-clean", "INC-04", [dec("d1"), call("t1"), inc("i1", [ref("t1")])], "conformant")
case("inc04-no-related", "INC-04", [inc("i1")], "conformant")
case("inc04-related-unresolved", "INC-04", [dec("d1"), inc("i1", [ref("d1"), ref("zz")])], "non-conformant", ["i1"])
case("inc04-related-dangling", "INC-04", [dec("d1"), call("t1", authorization=ref("zz")), inc("i1", [ref("t1")])],
     "non-conformant", ["i1"])
case("inc04-refs-dangling", "INC-04", [inc("i1", refs={"decision": ref("zz")})], "non-conformant", ["i1"])
case("inc04-depth-two", "INC-04", [policy("p1", refs={"request": ref("zz")}), dec("d1"),
     call("t1", authorization=ref("p1")), inc("i1", [ref("t1")])], "non-conformant", ["i1"])
case("inc04-order-reversed", "INC-04", [inc("i1", [ref("t1")]), dec("d1"), call("t1", authorization=ref("zz"))],
     "non-conformant", ["i1"])
case("inc04-one-of-two", "INC-04", [dec("d1"), inc("i1", [ref("d1")]), inc("i2", [ref("zz")])], "non-conformant",
     ["i2"])
case("inc04-no-incident", "INC-04", EP_CALL, "not_applicable")
# The catalog refuses a requires_denominator it cannot read, in every engine.
catalog_case("cat-not-object", '"egress_proxy_log"')
catalog_case("cat-missing-covers", "{kinds: [egress_proxy_log]}")
catalog_case("cat-extra-key", "{kinds: [egress_proxy_log], covers: ToolCall, min: 1}")
catalog_case("cat-empty-kinds", "{kinds: [], covers: ToolCall}")
catalog_case("cat-unknown-kind", "{kinds: [egress_log], covers: ToolCall}")
catalog_case("cat-repeated-kind", "{kinds: [egress_proxy_log, egress_proxy_log], covers: ToolCall}")
catalog_case("cat-empty-covers", "{kinds: [egress_proxy_log], covers: ''}")
catalog_case("cat-covers-not-string", "{kinds: [egress_proxy_log], covers: [ToolCall]}")
catalog_case("cat-kind-not-string", "{kinds: [1], covers: ToolCall}")

old = json.loads(Path("cases.json").read_text()) if Path("cases.json").exists() else {"cases": {}}
for name, c in C.items():
    c["expected"]["exit"] = old["cases"].get(name, {}).get("expected", {}).get("exit")
Path("cases.json").write_text(json.dumps({"cases": C}, indent=2) + "\n", encoding="utf-8")
print(len(C), "cases")
