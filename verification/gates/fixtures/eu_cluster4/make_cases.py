"""Write cases.json for VG-EUAIACT-CLUSTER4 (18.37g). Run from this folder: python make_cases.py.

Each case names one control, its events, the profile it runs under, how many records are quarantined, the
integrity strength of any source that is not export_anchored, and the expected outcome, failing events and assess
exit code. The exit codes are kept from the cases.json already written (captured from the real CLIs)."""

import json
from pathlib import Path

SUBJECT = "spiffe://corp/agents/eu-cluster4"
CTX = "https://agent-conformance.org/contexts/evidence/v1"
T0 = "2026-01-10T00:00:00Z"
EARLIER = "2026-01-09T00:00:00Z"
LATER = "2026-01-11T00:00:00Z"
CLASS = {"Decision": "self_report", "ModelCall": "self_report", "MemoryRead": "self_report"}
DIGEST = "sha256:" + "3" * 64
EP = "urn:src:enforcement_point"


def ev(i, t, at=T0, **data):
    cls = data.pop("cls", CLASS.get(t, "enforcement_point"))
    agent = data.pop("agent", {"id": SUBJECT})
    return {"specversion": "1.0", "id": i, "source": f"urn:src:{cls}", "subject": SUBJECT,
            "type": f"org.agent-conformance.evidence.{t}.v1", "datacontenttype": "application/ld+json",
            "agentcesourceclass": cls, "time": at,
            "data": {"@context": CTX, "@type": t, "agent": agent, **data}}


def ref(i):
    return f"agentce:event/{i}"


def dec(i="d1", dtype="dom:CreditDecision", **k):
    return ev(i, "Decision", decision_type=dtype, **k)


def mc(i="m1", **k):
    return ev(i, "ModelCall", operation="chat", model={"provider": "p", "name": "m", "version_or_digest": "1"}, **k)


def ra(i="r1", operation="read", **k):
    return ev(i, "ResourceAccess", resource={"uri": "db://loans/applicant", "kind": "table",
                                             "classification": "personal_data"}, operation=operation, **k)


def mr(i="r1", **k):
    return ev(i, "MemoryRead", store="vector", record_refs=["mem-1"], trust_min="trusted", **k)


def tc(i="t1", d="d1", **k):
    data = {"tool": {"name": "update_loan", "server": "loans", "protocol": "mcp", "version_or_digest": DIGEST},
            "args_ref": DIGEST, "result_ref": DIGEST, "side_effect": "write", "effect_class": "write",
            "refs": {"decision": ref(d), **k.pop("refs", {})}}
    data.update(k)
    return ev(i, "ToolCall", **data)


def dl(i="g1", expires=LATER, status="verified", at=T0, **k):
    data = {"token_ref": "jti-1", "issuer": "idp", "subject_principal": SUBJECT, "actor_principal": SUBJECT,
            "chain": [{"id": "user:alice", "kind": "human"}], "scope_granted": ["loans:write"],
            "expires": expires, "verification": {"status": status, "method": "rfc8693"}}
    data.update(k)
    return ev(i, "DelegationIssued", at=at, **data)


def delegated(i="t1", g=ref("g1"), d="d1", **k):
    return tc(i, d, refs={"delegation": g}, **k)


C = {}


def case(name, control, events, outcome, failing=(), quarantined=0, strength=None, profile="applicability.yaml"):
    C[name] = {"control": control, "events": events, "profile": profile, "quarantined": quarantined,
               "strength": strength or {}, "expected": {"outcome": outcome, "failing": list(failing), "exit": None}}


NC = "non-conformant"
CF = "conformant"
IE = "insufficient_evidence"
NA = "not_applicable"
NS = "not_assessed"

# REC-03 (Art. 12(2)): every input a consequential decision names is a held record, and it names at least one.
case("rec03-inputs-held", "REC-03", [mc(), ra(), dec(inputs=[ref("r1"), ref("m1")])], CF)
case("rec03-sr-input", "REC-03", [mc(), ra(cls="self_report"), dec(inputs=[ref("r1")])], CF)
case("rec03-no-inputs", "REC-03", [mc(), dec()], NC, ["d1"])
case("rec03-empty-inputs", "REC-03", [mc(), dec(inputs=[])], NC, ["d1"])
case("rec03-input-dangling", "REC-03", [mc(), ra(), dec(inputs=[ref("r1"), ref("zz")])], NC, ["d1"])
case("rec03-input-bare", "REC-03", [mc(), ra(), dec(inputs=["r1"])], NC, ["d1"])
case("rec03-input-quarantined", "REC-03", [mc(), ra(operation=7), dec(inputs=[ref("r1")])], NC, ["d1"],
     quarantined=1)
# The schema refuses a non-string input, so the decision is quarantined and nothing is judged.
case("rec03-input-not-string", "REC-03", [mc(), ra(), dec(inputs=[7])], IE, quarantined=1)
case("rec03-no-modelcall", "REC-03", [ra(), dec(inputs=[ref("r1")])], IE)
case("rec03-order-reversed", "REC-03", [dec(inputs=[ref("r1")]), ra(), mc()], CF)
case("rec03-one-of-two", "REC-03", [mc(), ra(), dec(inputs=[ref("r1")]), dec("d2")], NC, ["d2"])
case("rec03-not-consequential", "REC-03", [mc(), dec(dtype="dom:Lookup")], NA)

# DAT-01 (Art. 10): one input the decision names is a held data read: a ResourceAccess read or a MemoryRead.
case("dat01-resource-read", "DAT-01", [mc(), ra(), dec(inputs=[ref("r1")])], CF)
case("dat01-memory-read", "DAT-01", [mc(), mr(), dec(inputs=[ref("r1")])], CF)
case("dat01-held-beside-dangling", "DAT-01", [mc(), ra(), dec(inputs=[ref("r1"), ref("zz")])], CF)
case("dat01-modelcall-only", "DAT-01", [mc(), dec(inputs=[ref("m1")])], NC, ["d1"])
case("dat01-resource-write", "DAT-01", [mc(), ra(operation="write"), dec(inputs=[ref("r1")])], NC, ["d1"])
case("dat01-unnamed-read", "DAT-01", [mc(), ra(), dec(inputs=[ref("m1")])], NC, ["d1"])
case("dat01-no-inputs", "DAT-01", [mc(), ra(), dec()], NC, ["d1"])
case("dat01-dangling", "DAT-01", [mc(), dec(inputs=[ref("zz")])], NC, ["d1"])
case("dat01-bare", "DAT-01", [mc(), ra(), dec(inputs=["r1"])], NC, ["d1"])
case("dat01-read-quarantined", "DAT-01", [mc(), ra(operation=7), dec(inputs=[ref("r1")])], NC, ["d1"],
     quarantined=1)
case("dat01-no-modelcall", "DAT-01", [ra(), dec(inputs=[ref("r1")])], IE)
case("dat01-order-reversed", "DAT-01", [dec(inputs=[ref("r1")]), ra(), mc()], CF)
case("dat01-one-of-two", "DAT-01", [mc(), ra(), dec(inputs=[ref("r1")]), dec("d2", inputs=[ref("m1")])], NC,
     ["d2"])
case("dat01-not-consequential", "DAT-01", [mc(), dec(dtype="dom:Lookup")], NA)

# TRN-02 (Art. 86(1)): the agent that made the decision and the part the AI system played (ai_role).
for role in ("sole", "recommendation", "assist"):
    case(f"trn02-{role}", "TRN-02", [dec(ai_role=role)], CF)
case("trn02-no-role", "TRN-02", [dec()], NC, ["d1"])
case("trn02-no-agent", "TRN-02", [dec(ai_role="sole", agent=None)], NC, ["d1"])
# The schema refuses a role outside its enum, so the decision is quarantined and nothing is judged.
case("trn02-role-invalid", "TRN-02", [dec(ai_role="oracle")], IE, quarantined=1)
case("trn02-one-of-two", "TRN-02", [dec(ai_role="sole"), dec("d2")], NC, ["d2"])
case("trn02-not-consequential", "TRN-02", [dec(dtype="dom:Lookup")], NA)
# REC-01 (Art. 12) keeps the acting agent alone, so a decision with no role still meets it.
case("rec01-no-role", "REC-01", [dec()], CF)
case("rec01-no-agent", "REC-01", [dec(ai_role="sole", agent=None)], NC, ["d1"])

# INT-04 (engine integrity): an enforcement-point call carries an integrity block at a strength SPEC 6.6 can verify
# (source_signed or export_anchored); an independent-system call carries one at any strength.
case("int04-anchored", "INT-04", [dec(), tc()], CF)
# source_signed with no signature reads verified_weak, so the SPEC 6.6 cap reads partial; the rule itself accepts it.
case("int04-signed", "INT-04", [dec(), tc()], "partial", strength={EP: "source_signed"})
case("int04-chained", "INT-04", [dec(), tc()], NC, ["t1"], strength={EP: "export_chained"})
# The SPEC 6.6 cap only lowers a pass, so a call with no integrity block fails the rule and stays non-conformant.
case("int04-no-integrity", "INT-04", [dec(), tc()], NC, ["t1"], strength={EP: "none"})
case("int04-independent-chained", "INT-04", [dec(), tc(cls="independent_system")], CF,
     strength={"urn:src:independent_system": "export_chained"})
case("int04-independent-none", "INT-04", [dec(), tc(cls="independent_system")], NC, ["t1"],
     strength={"urn:src:independent_system": "none"})
case("int04-self-report", "INT-04", [dec(), tc(cls="self_report")], NC, ["t1"])
case("int04-order-reversed", "INT-04", [tc(), dec()], CF)
case("int04-one-of-two", "INT-04", [dec(), tc(), tc("t2", cls="independent_system")], NC, ["t2"],
     strength={"urn:src:independent_system": "none"})
case("int04-not-consequential", "INT-04", [dec(dtype="dom:Lookup"), tc()], NA)

# ROB-01 (Art. 15): the call passed a trusted source that classified its effect (effect_class, SPEC 6.2.3: from the
# gateway's tool registry).
case("rob01-classified", "ROB-01", [dec(), tc()], CF)
case("rob01-independent", "ROB-01", [dec(), tc(cls="independent_system")], CF)
case("rob01-no-effect-class", "ROB-01", [dec(), tc(effect_class=None)], NC, ["t1"])
case("rob01-effect-invalid", "ROB-01", [dec(), tc(effect_class="teleport")], IE, quarantined=1)
case("rob01-self-report", "ROB-01", [dec(), tc(cls="self_report")], NC, ["t1"])
# An export_chained enforcement point is verified_weak: the SPEC 6.6 cap reads partial where INT-04 reads non-conformant.
case("rob01-chained-classified", "ROB-01", [dec(), tc()], "partial", strength={EP: "export_chained"})
case("rob01-order-reversed", "ROB-01", [tc(), dec()], CF)
case("rob01-one-of-two", "ROB-01", [dec(), tc(), tc("t2", effect_class=None)], NC, ["t2"])
case("rob01-not-consequential", "ROB-01", [dec(dtype="dom:Lookup"), tc(effect_class=None)], NA)

# ROB-04 (Art. 15): the call ran under a held, verified delegation whose expiry is after the call's time (RFC 7519
# 4.1.4: not accepted on or after exp), compared as instants (SPEC 6.7).
case("rob04-in-force", "ROB-04", [dl(), dec(), delegated()], CF)
case("rob04-expired", "ROB-04", [dl(expires=EARLIER), dec(), delegated()], NC, ["t1"])
case("rob04-expires-at-call", "ROB-04", [dl(expires=T0), dec(), delegated()], NC, ["t1"])
case("rob04-expires-offset-before", "ROB-04", [dl(expires="2026-01-10T01:00:00+02:00"), dec(), delegated()], NC,
     ["t1"])
case("rob04-expires-offset-after", "ROB-04", [dl(expires="2026-01-10T01:00:00-02:00"), dec(), delegated()], CF)
case("rob04-no-expires", "ROB-04", [dl(expires=None), dec(), delegated()], NC, ["t1"])
case("rob04-expires-unplaceable", "ROB-04", [dl(expires="next tuesday"), dec(), delegated()], NC, ["t1"])
case("rob04-unverified", "ROB-04", [dl(status="failed"), dec(), delegated()], NC, ["t1"])
case("rob04-dangling", "ROB-04", [dl(), dec(), delegated(g=ref("zz"))], NC, ["t1"])
case("rob04-bare", "ROB-04", [dl(), dec(), delegated(g="g1")], NC, ["t1"])
case("rob04-wrong-type", "ROB-04", [dl(), dec(), delegated(g=ref("d1"))], NC, ["t1"])
case("rob04-no-delegation", "ROB-04", [dl(), dec(), tc()], NC, ["t1"])
# A delegation issued after the call did not cover it (SPEC 6.2.1: the event time is the issuance); it comes last so
# its stream stays in time order.
case("rob04-issued-after-call", "ROB-04", [dec(), delegated(), dl(at=LATER, expires="2026-01-12T00:00:00Z")], NC, ["t1"])
case("rob04-sr-delegation", "ROB-04", [dl(cls="self_report"), dec(), delegated()], CF)
case("rob04-order-reversed", "ROB-04", [delegated(), dec(), dl()], CF)
case("rob04-one-of-two", "ROB-04", [dl(), dl("g2", expires=EARLIER), dec(), delegated(), delegated("t2", g=ref("g2"))], NC,
     ["t2"])
case("rob04-not-consequential", "ROB-04", [dl(expires=EARLIER), dec(dtype="dom:Lookup"), delegated()], NA)
# OVS-04 (Art. 14(4)(c)-(d)) keeps the verified delegation alone, so an expired one still meets it.
case("ovs04-expired", "OVS-04", [dl(expires=EARLIER), dec(), delegated()], CF)
case("ovs04-unverified", "OVS-04", [dl(status="failed"), dec(), delegated()], NC, ["t1"])

# TRN-03 (Art. 26(11), 86): a decision's rationale_claim_ref and its notice's content_ref, when written as a Ref
# (SPEC 3), name a held event (18.37h); a Digest, a URI or a bare id names no event and is not judged. Any one clean
# notice is enough; a dangling rationale fails whatever the notices.
def trn_dec(i="d1", **k):
    return dec(i, ai_role="sole", **k)


def notice(i="n1", d="d1", notice_type="subject_of_ai_decision", **k):
    return ev(i, "Notice", cls="independent_system", person_ref="p-1", notice_type=notice_type, delivered_at=T0,
              channel="email", refs={"decision": ref(d)}, **k)


case("trn03-clean", "TRN-03", [trn_dec(), notice()], CF)
case("trn03-rationale-held", "TRN-03", [mc(), trn_dec(rationale_claim_ref=ref("m1")), notice()], CF)
case("trn03-rationale-self", "TRN-03", [trn_dec(rationale_claim_ref=ref("d1")), notice()], CF)
case("trn03-rationale-dangling", "TRN-03", [trn_dec(rationale_claim_ref=ref("zz")), notice()], NC, ["d1"])
case("trn03-rationale-dangling-late-notice", "TRN-03", [notice(), trn_dec(rationale_claim_ref=ref("zz"))], NC,
     ["d1"])
case("trn03-rationale-digest", "TRN-03", [trn_dec(rationale_claim_ref=DIGEST), notice()], CF)
case("trn03-rationale-blob", "TRN-03", [trn_dec(rationale_claim_ref="blob://rationale/1"), notice()], CF)
# The ModelCall's operation fails the schema, so it is quarantined and not held; it sits in its own stream so the
# decision's chain still verifies.
case("trn03-rationale-to-quarantined", "TRN-03",
     [ev("m1", "ModelCall", cls="enforcement_point", operation=7), trn_dec(rationale_claim_ref=ref("m1")), notice()], NC, ["d1"],
     quarantined=1)
case("trn03-rationale-bare-id", "TRN-03", [mc(), trn_dec(rationale_claim_ref="m1"), notice()], CF)
case("trn03-content-held", "TRN-03", [mc(), trn_dec(), notice(content_ref=ref("m1"))], CF)
case("trn03-content-dangling", "TRN-03", [trn_dec(), notice(content_ref=ref("zz"))], NC, ["d1"])
case("trn03-content-blob", "TRN-03", [trn_dec(), notice(content_ref="blob://notice/1")], CF)
case("trn03-content-digest", "TRN-03", [trn_dec(), notice(content_ref=DIGEST)], CF)
case("trn03-explanation-notice-content-dangling", "TRN-03",
     [trn_dec(), notice(notice_type="explanation", content_ref=ref("zz"))], NC, ["d1"])
case("trn03-two-notices-one-clean", "TRN-03", [trn_dec(), notice(content_ref=ref("zz")), notice("n2")], CF)
case("trn03-two-notices-one-clean-reversed", "TRN-03", [trn_dec(), notice("n2"), notice(content_ref=ref("zz"))], CF)
case("trn03-two-notices-both-dangling", "TRN-03",
     [trn_dec(), notice(content_ref=ref("zz")), notice("n2", content_ref=ref("yy"))], NC, ["d1"])
case("trn03-one-of-two", "TRN-03",
     [trn_dec(), notice(), trn_dec("d2", rationale_claim_ref=ref("zz")), notice("n2", d="d2")], NC, ["d2"])
# TRN-03's minimum evidence names a Notice.
case("trn03-no-notice", "TRN-03", [trn_dec()], IE)
case("trn03-not-consequential", "TRN-03", [trn_dec(dtype="dom:Lookup", rationale_claim_ref=ref("zz"))], NA)

# DAT-02 and DOC-04 are rung-3 measurements against declared thresholds that no engine makes yet.
case("dat02-not-assessed", "DAT-02", [mc(), ra(), dec(inputs=[ref("r1")])], NS)
case("doc04-not-assessed", "DOC-04", [dec(ai_role="sole")], NS)

# A None member is left out of the record (the helpers pass None to drop a member).
for c in C.values():
    for e in c["events"]:
        e["data"] = {k: v for k, v in e["data"].items() if v is not None}

old = json.loads(Path("cases.json").read_text()) if Path("cases.json").exists() else {"cases": {}}
for name, c in C.items():
    c["expected"]["exit"] = old["cases"].get(name, {}).get("expected", {}).get("exit")
Path("cases.json").write_text(json.dumps({"cases": C}, indent=2) + "\n", encoding="utf-8")
print(len(C), "cases")
