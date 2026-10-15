"""Write cases.json for VG-DUPLICATE-ID-CLASS (18.143). Run from this folder: python make_cases.py.

Ingest keeps one record per event id. A record from a lower source class never displaces a record of a higher class
that shares its id (the evidence ladder: independent_system above enforcement_point above self_report, ranked on the
class ingest trusts, so an undeclared source's self-asserted class counts as self_report, SPEC §6.4): the lower record
is quarantined as duplicate_id wherever it sits in the file, and the higher record is assessed as if it were absent.
Two records of one class with one id stay a duplicate_id quarantine of the later one, as before. Each shadow case reads
exactly like its no-shadow twin, plus one quarantined record. Event times stay non-decreasing within each stream (one
per source), so ingest quarantines nothing for order, except in the time-order cases, which put the enforcement point's
ToolCall out of order on purpose."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from approval_events import NC, appr, case, dec, idp, ran, write  # noqa: E402


def shadow(i, cls="self_report", source=None):
    """A Decision with id i from source class cls (the agent's own record, by default)."""
    e = dec(i, cls=cls)
    if source is not None:
        e["source"] = source
    return e


# The repro: the agent's own Decision reuses the enforcement point's ToolCall id.
case(
    "cnd02-toolcall-no-shadow",
    "CND-02",
    [dec(), ran(), idp(), appr("reject")],
    NC,
    ["d1"],
)
case(
    "cnd02-self-report-shadows-toolcall",
    "CND-02",
    [shadow("t0"), dec(), ran(), idp(), appr("reject")],
    NC,
    ["d1"],
    quarantined=1,
)
# The same record after the ToolCall (already quarantined before 18.143).
case(
    "cnd02-self-report-after-toolcall",
    "CND-02",
    [dec(), ran(), shadow("t0"), idp(), appr("reject")],
    NC,
    ["d1"],
    quarantined=1,
)
# An undeclared source asserting independent_system is trusted as self_report, so it ranks below the ToolCall.
case(
    "cnd02-unclassed-source-shadows-toolcall",
    "CND-02",
    [shadow("t0", cls="independent_system", source="urn:src:unclassed"), dec(), ran(), idp(), appr("reject")],
    NC,
    ["d1"],
    quarantined=1,
)
# The agent's own Decision reuses the identity provider's login id.
# The login keeps its id; the quarantined Decision is a decision with no approval, so judged as the record it
# is it would fail the control, and quarantine never gives a better outcome (18.119): insufficient_evidence.
case(
    "cnd02-login-no-shadow",
    "CND-02",
    [dec(), ran(), idp(), appr("approve")],
    "conformant",
)
case(
    "cnd02-self-report-shadows-login",
    "CND-02",
    [shadow("s1"), dec(), ran(), idp(), appr("approve")],
    "insufficient_evidence",
    quarantined=1,
)
# An enforcement-point record ranks below the identity provider's independent_system login. The shadowing ToolCall
# only reads: an irreversible one, withheld, would read non-conformant with the held records (18.119), so the case
# would judge that instead of the id's owner.
READ_S1 = ran("d9", "s1")
READ_S1["data"]["effect_class"] = "read"
case(
    "cnd02-enforcement-point-shadows-login",
    "CND-02",
    [dec(), READ_S1, ran(), idp(), appr("approve")],
    "conformant",
    quarantined=1,
)
case(
    "ovs08-self-report-shadows-login",
    "OVS-08",
    [shadow("s1"), dec(), idp(), appr("approve")],
    "insufficient_evidence",
    quarantined=1,
)
# One class, one id: the later record is quarantined, as before.
case(
    "cnd02-same-class-duplicate-toolcall",
    "CND-02",
    [dec(), ran(), ran("d9"), idp(), appr("reject")],
    NC,
    ["d1"],
    quarantined=1,
)

# Quarantine state: the higher-class ToolCall is quarantined for time order, and the lower record still never takes
# its id; with no action left, CND-02 reads as its twin without the shadow. t0's CND-02 gate, recorded at its time after
# t9, is quarantined with it, and t9 executes no held decision (d9), so t9 is the failing action.
def late(e, t):
    e["time"] = t
    return e


case(
    "cnd02-toolcall-time-order-no-shadow",
    "CND-02",
    [dec(), late(ran("d9", "t9"), "2026-01-10T02:00:00Z"), late(ran(), "2026-01-10T01:00:00Z"), idp(), appr("reject")],
    NC,
    ["t9"],
    quarantined=2,
)
case(
    "cnd02-self-report-shadows-time-order-toolcall",
    "CND-02",
    [shadow("t0"), dec(), late(ran("d9", "t9"), "2026-01-10T02:00:00Z"), late(ran(), "2026-01-10T01:00:00Z"), idp(), appr("reject")],
    NC,
    ["t9"],
    quarantined=3,
)
# The lower record after the time-quarantined ToolCall: the owner is fixed before time order, so arrival order does
# not hand it the id either.
case(
    "cnd02-self-report-after-time-order-toolcall",
    "CND-02",
    [dec(), late(ran("d9", "t9"), "2026-01-10T02:00:00Z"), late(ran(), "2026-01-10T01:00:00Z"), late(shadow("t0"), "2026-01-10T03:00:00Z"), idp(), appr("reject")],
    NC,
    ["t9"],
    quarantined=3,
)


# A record whose class is off the ladder fails the schema and holds no id: the valid lower record keeps it, as before.
def invalid(e):
    e["agentcesourceclass"] = "gateway"
    return e


case(
    "cnd02-self-report-and-invalid-toolcall",
    "CND-02",
    [shadow("t0"), dec(), invalid(ran()), idp(), appr("reject")],
    "insufficient_evidence",
    quarantined=1,
)

# A record declaring a class its source is not declared with is quarantined class_mismatch and holds no id.
case(
    "cnd02-class-mismatch-shadows-toolcall",
    "CND-02",
    [shadow("t0", cls="enforcement_point", source="urn:src:self_report"), dec(), ran(), idp(), appr("reject")],
    NC,
    ["d1"],
    quarantined=1,
)

write()
