"""Write cases.json for VG-QUARANTINE-NO-PASS (18.119). Run from this folder: python make_cases.py.

Quarantine never gives a better outcome. A record ingest refuses for where it sits (time_order, a duplicate_id that
is not an identical redelivery, class_mismatch, unknown_source) is still readable: when judging it with the subject's held records changes a
conformant or not_applicable outcome, the control reads insufficient_evidence naming it. A record ingest cannot read
(schema_invalid) may hold anything: every conformant or not_applicable outcome of its subject (of every subject, when
the record names none) reads insufficient_evidence. A benign quarantine (an identical redelivery, an unknown type, a
late record that changes nothing, an unreadable record of another subject) leaves the outcome as it is. Each case
names the fixture folder whose profile and domain it is assessed under."""

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from approval_events import CTX, NC, TWO, C, appr, dec, ran, write  # noqa: E402
from approval_events import SUBJECT as OVS  # noqa: E402

IE = "insufficient_evidence"



def case(
    name,
    fixture,
    subject,
    control,
    events,
    outcome,
    failing=(),
    quarantined=0,
    exit=None,
    evidence=None,
    withheld=False,
    fail_on=None,
):
    """``evidence`` is the quarantined event ids an insufficient_evidence case names (an unreadable line names none).
    ``withheld`` says the --json output lists the case control under withheld_non_conformant: its withheld records
    read non-conformant with the held ones, so assess exits as for a non-conformant outcome (18.149). ``fail_on`` is a
    --fail-on expression the case is assessed with."""
    if exit is None:
        # The approval fixture's profile holds other high-severity controls these bundles leave unmet (exit 2).
        exit = 2 if fixture == "approval_outcome" else 0
    C[name] = {
        "fixture": fixture,
        "subject": subject,
        "control": control,
        "events": events,
        "expected": {
            "outcome": outcome,
            "failing": list(failing),
            "quarantined": quarantined,
            "exit": exit,
            "withheld": withheld,
        },
    }
    if outcome == IE:
        C[name]["expected"]["evidence"] = list(evidence)
    if fail_on is not None:
        C[name]["fail_on"] = fail_on


def ev(subject, i, t, at, cls, **data):
    return {
        "specversion": "1.0",
        "id": i,
        "source": f"urn:src:{cls}",
        "subject": subject,
        "type": f"org.agent-conformance.evidence.{t}.v1",
        "datacontenttype": "application/ld+json",
        "agentcesourceclass": cls,
        "time": at,
        "data": {"@context": CTX, "@type": t, **data},
    }


# ROB-02 (baseline catalog): a guard writes mem:r1 trusted, later rules it untrusted; the untrusted write reaches the
# bundle after a later event of the guard's stream, so ingest quarantines it for time_order.
ROB = "spiffe://corp/agents/rob02-fixture"


def rob(i, t, sec, cls="enforcement_point", **data):
    return ev(ROB, i, t, f"2026-01-02T00:00:{sec:02d}Z", cls, **data)


def write_(i, sec, record, trust, verdict):
    return rob(i, "MemoryWrite", sec, store="kb", record_ref=record, trust=trust, guard_verdict=verdict)


W1 = write_("w1", 1, "mem:r1", "trusted", "allow")
W2 = write_("w2", 2, "mem:r1", "untrusted", "block")
READ = rob("m1", "MemoryRead", 3, store="kb", record_refs=["mem:r1"], refs={"consumer": "agentce:event/d1"})
DECIDE = rob(
    "d1", "Decision", 4, cls="self_report", decision_type="dom:CreditDecision", agent={"id": ROB}, inputs=["mem:r1"]
)
# Delivered on time the write makes ROB-02 non-conformant (exit 1), so delivered late it exits 1 too (18.149).
LATE_WRITE = [W1, READ, W2, DECIDE]
case(
    "rob02-untrusted-write-quarantined-time-order",
    "rob02_untrusted",
    ROB,
    "ROB-02",
    LATE_WRITE,
    IE,
    quarantined=1,
    exit=1,
    evidence=["w2"],
    withheld=True,
)
# A --fail-on expression that names non-conformant outcomes matches the late write as the outcome it would be on time.
case(
    "rob02-late-write-fail-on-non-conformant",
    "rob02_untrusted",
    ROB,
    "ROB-02",
    LATE_WRITE,
    IE,
    quarantined=1,
    exit=1,
    evidence=["w2"],
    withheld=True,
    fail_on='outcome=="non-conformant"',
)
# The same late write holding a lone surrogate, which UTF-8 cannot carry: unreadable, it has no canonical form, so
# no engine names a digest for it, and the control still does not pass.
surrogate = copy.deepcopy(W2)
surrogate["data"]["note"] = "\ud800"
case(
    "rob02-late-write-holds-a-lone-surrogate",
    "rob02_untrusted",
    ROB,
    "ROB-02",
    [W1, READ, surrogate, DECIDE],
    IE,
    quarantined=1,
    exit=2,
    evidence=[],
)
# An unreadable line beside the late write (it names no subject, so it counts for every subject) does not turn the
# exit rule off: the readable late write still reads non-conformant with the held records, so exit_codes carries 1
# beside the 2 the unreadable line gives; the in-order twin with the same line carries 1 for its non-conformant outcome.
BAD_LINE = '{"id": "zz", "type": '
case(
    "rob02-late-write-beside-an-unreadable-line",
    "rob02_untrusted",
    ROB,
    "ROB-02",
    [*LATE_WRITE, BAD_LINE],
    IE,
    quarantined=2,
    exit=2,
    evidence=["w2"],
    withheld=True,
)
case(
    "rob02-in-order-write-beside-an-unreadable-line",
    "rob02_untrusted",
    ROB,
    "ROB-02",
    [W1, W2, READ, DECIDE, BAD_LINE],
    NC,
    ["d1"],
    quarantined=1,
    exit=2,
)
case(
    "rob02-untrusted-write-in-order",
    "rob02_untrusted",
    ROB,
    "ROB-02",
    [W1, W2, READ, DECIDE],
    NC,
    ["d1"],
    exit=1,
)
# The guard's read supplies ROB-02's minimum evidence, so without it the held records read insufficient_evidence, not
# conformant: delivered after a later write of the guard's stream it is still judged with them, reads non-conformant
# and exits 1 as on time (18.149).
LATER_WRITE = write_("w3", 5, "mem:r9", "trusted", "allow")
case(
    "rob02-late-read-supplies-minimum-evidence",
    "rob02_untrusted",
    ROB,
    "ROB-02",
    [W1, W2, DECIDE, LATER_WRITE, READ],
    IE,
    quarantined=1,
    exit=1,
    evidence=["m1"],
    withheld=True,
)
case(
    "rob02-read-supplies-minimum-evidence-in-order",
    "rob02_untrusted",
    ROB,
    "ROB-02",
    [W1, W2, READ, DECIDE, LATER_WRITE],
    NC,
    ["d1"],
    exit=1,
)
case(
    "rob02-benign-time-order",
    "rob02_untrusted",
    ROB,
    "ROB-02",
    [W1, READ, write_("w2", 2, "mem:r9", "untrusted", "block"), DECIDE],
    "conformant",
    quarantined=1,
)
case(
    "rob02-benign-fail-on-non-conformant",
    "rob02_untrusted",
    ROB,
    "ROB-02",
    [W1, READ, write_("w2", 2, "mem:r9", "untrusted", "block"), DECIDE],
    "conformant",
    quarantined=1,
    fail_on='outcome=="non-conformant"',
)

# OVS-07 (eu-ai-act): its only target record, an Override, fails the schema; the control has records it cannot read,
# so it does not read not_applicable.
override = ev(
    OVS,
    "v1",
    "Override",
    "2026-01-10T00:00:00Z",
    "independent_system",
    refs={"decision": "agentce:event/d1"},
    actor={"id": "u2", "kind": "human"},
    session_ref="agentce:event/s1",
    original="approve",
    replacement="reject",
)
bad_override = copy.deepcopy(override)
bad_override["data"]["reason_code"] = 7
case(
    "ovs07-only-target-schema-invalid",
    "approval_outcome",
    OVS,
    "OVS-07",
    [dec(), *TWO, bad_override],
    IE,
    quarantined=1,
    evidence=["v1"],
)
bad_interrupt = ev(
    OVS,
    "x1",
    "Interrupt",
    "2026-01-10T00:00:00Z",
    "enforcement_point",
    actor={"id": "u2", "kind": "human"},
    session_ref="agentce:event/s1",
    mechanism="kill_switch",
    effect=["halted"],
)
case(
    "ovs07-only-interrupt-schema-invalid",
    "approval_outcome",
    OVS,
    "OVS-07",
    [dec(), *TWO, bad_interrupt],
    IE,
    quarantined=1,
    evidence=["x1"],
)

# CND-05 (Conduct overlay): a ToolCall whose refs.instruction is an array or an {"@id": ...} object fails the schema.
CND = "spiffe://corp/agents/cnd05-fixture"


def instruction():
    return ev(CND, "i1", "Instruction", "2026-01-02T00:00:01Z", "enforcement_point", source_class="tool_output")


def call(refs):
    return ev(
        CND,
        "tc1",
        "ToolCall",
        "2026-01-02T00:00:02Z",
        "enforcement_point",
        agent={"id": CND},
        tool={"name": "lookup", "protocol": "native"},
        refs={"instruction": refs},
    )


case(
    "cnd05-refs-instruction-array",
    "cnd05_chain",
    CND,
    "CND-05",
    [instruction(), call(["agentce:event/i1"])],
    IE,
    quarantined=1,
    exit=2,
    evidence=["tc1"],
)
case(
    "cnd05-refs-instruction-id-object",
    "cnd05_chain",
    CND,
    "CND-05",
    [instruction(), call({"@id": "agentce:event/i1"})],
    IE,
    quarantined=1,
    exit=2,
    evidence=["tc1"],
)
case(
    "cnd05-refs-instruction-held",
    "cnd05_chain",
    CND,
    "CND-05",
    [instruction(), call("agentce:event/i1")],
    NC,
    ["tc1"],
    exit=2,
)


# CND-02 (Conduct overlay): d1's own ToolCall reaches the bundle after d2's later one in the enforcement point's
# stream, so ingest quarantines it for time_order and the approve recorded after it looks like it came first.
def at(e, t):
    e["time"] = f"2026-01-10T{t}Z"
    return e


LATE_ACTION = [
    dec(),
    dec("d2"),
    at(ran("d2", "t2"), "03:00:00"),
    at(ran("d1", "t0"), "01:00:00"),
    *TWO,
    at(appr("approve", i="a2", d="d2"), "02:00:00"),
    at(appr("approve"), "02:00:00"),
]
case("cnd02-approve-after-action-quarantined-time-order", "approval_outcome", OVS, "CND-02", LATE_ACTION, IE, quarantined=1, evidence=["t0"], withheld=True)
case(
    "cnd02-reject-spelled-Reject",
    "approval_outcome",
    OVS,
    "CND-02",
    [dec(), *TWO, at(appr("approve"), "01:00:00"), at(appr("Reject", i="a2"), "02:00:00"), at(ran(), "03:00:00")],
    IE,
    quarantined=1,
    evidence=["a2"],
)
case(
    "cnd02-reject-shares-the-approve-id",
    "approval_outcome",
    OVS,
    "CND-02",
    [dec(), *TWO, at(appr("approve"), "01:00:00"), at(appr("reject"), "02:00:00"), at(ran(), "03:00:00")],
    IE,
    quarantined=1,
    evidence=["a1"],
    withheld=True,
)
GATED = [dec(), *TWO, at(appr("approve"), "01:00:00"), at(ran(), "03:00:00")]
case("cnd02-gated", "approval_outcome", OVS, "CND-02", GATED, "conformant")
# The same human's reject on a stream that claims another class than the manifest declares (class_mismatch): read with
# the declared class, it withdraws the approve.
mismatch = at(appr("reject", i="a2"), "02:00:00")
mismatch["agentcesourceclass"] = "enforcement_point"
case(
    "cnd02-reject-class-mismatch",
    "approval_outcome",
    OVS,
    "CND-02",
    [dec(), *TWO, at(appr("approve"), "01:00:00"), mismatch, at(ran(), "03:00:00")],
    IE,
    quarantined=1,
    evidence=["a2"],
    withheld=True,
)
# The same reject from a source the manifest does not declare (unknown_source) is read as self_report, as an
# undeclared source's record is (SPEC §6.4); the same human's reject still withdraws the approve.
undeclared = at(appr("reject", i="a2"), "02:00:00")
undeclared["source"] = "urn:src:undeclared"
case(
    "cnd02-reject-unknown-source",
    "approval_outcome",
    OVS,
    "CND-02",
    [dec(), *TWO, at(appr("approve"), "01:00:00"), undeclared, at(ran(), "03:00:00")],
    IE,
    quarantined=1,
    evidence=["a2"],
    withheld=True,
)
case(
    "cnd02-identical-redelivery-is-benign",
    "approval_outcome",
    OVS,
    "CND-02",
    [*GATED, at(appr("approve"), "01:00:00")],
    "conformant",
    quarantined=1,
)
heartbeat = at(ran("d1", "h1"), "03:00:00")
heartbeat["type"] = "org.agent-conformance.evidence.Heartbeat.v1"
case("cnd02-unknown-type-is-benign", "approval_outcome", OVS, "CND-02", [*GATED, heartbeat], "conformant", quarantined=1)
other = at(appr("Reject", i="a9"), "04:00:00")
other["subject"] = "spiffe://corp/agents/someone-else"
case(
    "cnd02-unreadable-record-of-another-subject",
    "approval_outcome",
    OVS,
    "CND-02",
    [*GATED, other],
    "conformant",
    quarantined=1,
)
case(
    "cnd02-unreadable-line-names-no-subject",
    "approval_outcome",
    OVS,
    "CND-02",
    [*GATED, '{"id": "a9", "type": '],
    IE,
    quarantined=1,
    evidence=[],
)

write()
